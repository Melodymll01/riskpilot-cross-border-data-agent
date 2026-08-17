"""Request-scoped 上下文（Step 025d / 025g）。

放两个 ``ContextVar``：``request_id_var`` 与 ``user_id_var``。HTTP middleware
在请求进入时 ``set(...)``、退出时 ``reset(...)``；use case 通过
``get_request_id()`` / ``get_user_id()`` 读取，让审计条目和 log 行自动带上
两个字段，而不需要每个调用点都改签名透传。

设计要点：
- ``request_id`` 在 use case 显式形参里仍然保留（命令行/后台任务可以显式给）；
  实际语义为"显式形参优先 > contextvar > None"，由 use case 内部的
  ``_record_audit`` 合并。
- ``user_id`` 只走 contextvar 一条路（API 层通过 cookie 解析后 set；离线
  脚本不设即 ``None``）。审计写入时仍然显式传 ``actor_id``，contextvar 仅
  用于 logging filter 注入 ``record.user_id``。
- 两个 ``ContextVar`` 默认值都是 ``None``，绝不抛 ``LookupError``。
- ``anyio.to_thread.run_sync`` / ``asyncio.to_thread`` 在 Python 3.9+ 会用
  ``contextvars.copy_context().run(...)`` 透传，所以 use case 用线程池
  跑同步代码（如 chroma 写）时也能读到。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

# 默认 None：在 middleware 之外的调用方（命令行、后台任务、单测）不会拿到陈旧值。
request_id_var: ContextVar[str | None] = ContextVar("request_id_var", default=None)
user_id_var: ContextVar[str | None] = ContextVar("user_id_var", default=None)


def get_request_id() -> str | None:
    """读取当前 request_id；middleware 外或未设置时返回 ``None``。"""
    return request_id_var.get()


def set_request_id(value: str | None) -> object:
    """设置 request_id，返回 reset token（middleware 退出时调 reset）。

    ``value`` 允许 ``None``——把当前 task 显式置空（极少用，通常是测试辅助）。
    """
    return request_id_var.set(value)


def reset_request_id(token: object) -> None:
    """根据 ``set_request_id`` 返回的 token 还原上一层值。"""
    request_id_var.reset(token)  # type: ignore[arg-type]


def get_user_id() -> str | None:
    """读取当前 user_id；middleware 外或未登录时返回 ``None``。"""
    return user_id_var.get()


def set_user_id(value: str | None) -> object:
    """设置 user_id，返回 reset token（与 ``set_request_id`` 对称）。"""
    return user_id_var.set(value)


def reset_user_id(token: object) -> None:
    """根据 ``set_user_id`` 返回的 token 还原上一层值。"""
    user_id_var.reset(token)  # type: ignore[arg-type]


@contextmanager
def request_context(
    request_id: str | None,
    *,
    user_id: str | None = None,
) -> Iterator[None]:
    """便利 contextmanager：``with request_context("req-x", user_id="u1"): ...``。

    primarily 给测试和命令行场景用；HTTP middleware 仍走 set/reset 因为
    生命周期跨 ``await call_next``，contextmanager 形式不直观。

    ``user_id`` 默认 ``None``——只设 request_id 不设 user_id，与 Step 025d
    既有签名向后兼容（位置参数仍是 request_id）。
    """
    rid_token = set_request_id(request_id)
    uid_token = set_user_id(user_id)
    try:
        yield
    finally:
        reset_user_id(uid_token)
        reset_request_id(rid_token)


def install_request_id_middleware(app: FastAPI) -> None:
    """注册 FastAPI middleware，把请求 ``X-Request-ID``（或自动生成）写入 contextvar。

    设计：
    - 读 ``X-Request-ID`` header；无则用 ``uuid4().hex[:12]`` 自动生成
    - 同时写到 ``request.state.request_id``（向后兼容 main.py 老 inline middleware）
    - 在 ``finally`` 里 ``reset`` 防止 contextvar 跨请求泄漏
    - 在 response 上回写 ``X-Request-ID`` header，方便客户端把 id 反馈给运维

    main.py 在自己的 inline middleware 里手工做了同样的 set/reset，不再调本函数；
    本函数留给测试 fixture / 其它独立 FastAPI app 复用。
    """

    @app.middleware("http")
    async def _request_id_middleware(request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex[:12])
        request.state.request_id = request_id
        token = set_request_id(request_id)
        try:
            response = await call_next(request)
        finally:
            reset_request_id(token)
        response.headers["X-Request-ID"] = request_id
        return response
