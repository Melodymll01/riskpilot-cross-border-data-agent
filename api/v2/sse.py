"""``AgentEvent`` → Server-Sent Events 帧序列化。

SSE 格式（RFC 见 W3C SSE 规范）：
```
event: <event_type>
data: <single-line JSON>

```

要点：
- 每帧以 ``\\n\\n`` 结束
- ``data:`` 内禁止内嵌裸 ``\\n``；我们 dump_json 后用 ``\\n`` 替成空格
- 长时间无事件时发心跳注释 ``: ping\\n\\n``，防 nginx/cloudflare 60s 切流

中间 keepalive 由 ``stream_with_keepalive`` 包装器实现：
- 把同步生成器跑在 threadpool（``asyncio.to_thread``）
- 用 ``asyncio.wait`` 让事件 / 心跳两路赛跑
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from domain.agent import AgentEvent


def event_to_sse(event: AgentEvent) -> str:
    """把单个 ``AgentEvent`` 序列化为 SSE 帧字符串。"""
    body = json.dumps(event.payload, ensure_ascii=False, default=str)
    # data 字段不能含裸换行——SSE 规范用 \n 作字段分隔
    body = body.replace("\n", " ")
    return f"event: {event.event_type.value}\ndata: {body}\n\n"


def sse_keepalive() -> str:
    """SSE 心跳：注释行（以 ``:`` 开头），客户端忽略但代理会刷新连接。"""
    return ": keepalive\n\n"


def sse_error(error_code: str, message: str) -> str:
    """以 SSE 帧形式回报错误（用于 Agent 流中途异常时）。"""
    body = json.dumps(
        {"error_code": error_code, "message": message}, ensure_ascii=False
    )
    return f"event: error\ndata: {body}\n\n"


# StopIteration / 结束哨兵——不能用 ``None``（事件 payload 可能合法为 None）
_STREAM_END: object = object()


async def stream_with_keepalive(
    sync_events: Iterator[AgentEvent], *, keepalive_seconds: float
) -> AsyncIterator[str]:
    """把同步 ``Iterator[AgentEvent]`` 包成异步 SSE 字符串流。

    - 同步生成器跑在 threadpool（``loop.run_in_executor``），避免阻塞事件循环
    - 等待事件期间每 ``keepalive_seconds`` 秒发一帧心跳
    - 任一事件 raise 时翻译成 ``event: error`` 帧，再正常关闭流

    .. warning::
       关键点：keepalive 超时**不能** ``cancel`` 后再新建 task。
       ``Future.cancel()`` 对已在 executor 线程里执行的同步 ``next(iterator)``
       不会真正中断；若下一轮 while 再 ``loop.run_in_executor(None, _next)``，
       同一个 generator 会被两条线程并发驱动，触发
       ``ValueError: generator already executing``。
       正确做法是 **跨循环复用同一个 task**：超时只发心跳，task 留着继续等。
    """
    loop = asyncio.get_running_loop()
    iterator = iter(sync_events)

    def _next() -> object:
        """同步 next；走到尾返回 ``_STREAM_END`` 哨兵。"""
        try:
            return next(iterator)
        except StopIteration:
            return _STREAM_END

    task: asyncio.Future[object] | None = None
    try:
        while True:
            if task is None:
                task = loop.run_in_executor(None, _next)
            # 用 wait 而非 wait_for，超时不会 cancel task —— 它继续在后台跑
            done, _pending = await asyncio.wait({task}, timeout=keepalive_seconds)
            if not done:
                # 等事件超时：发心跳，下一轮继续等同一个 task
                yield sse_keepalive()
                continue

            current_task = task
            task = None  # 取完结果后置空，下一轮 while 才会启动新的 _next()
            try:
                ev = current_task.result()
            except Exception as exc:  # noqa: BLE001 — agent 内部异常都翻译成 SSE error
                yield sse_error("AGENT_EXCEPTION", f"{type(exc).__name__}: {exc}")
                return

            if ev is _STREAM_END:
                return
            yield event_to_sse(ev)  # type: ignore[arg-type]
    finally:
        # 客户端断流 / 上游异常：尽力 cancel；executor 线程的 next() 仍可能跑完
        if task is not None and not task.done():
            task.cancel()
