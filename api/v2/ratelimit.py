"""v2 HTTP 限流（基于 ``limits`` moving-window，以 FastAPI 依赖形式接入）。

接 Step 029「v2 限流列后续」遗留项。

为什么用依赖而非 slowapi 装饰器：
- 本仓库所有路由文件都带 ``from __future__ import annotations``（PEP 563 字符串注解）。
  slowapi 的 ``@limiter.limit`` 会用 ``functools.wraps`` 包裹端点，FastAPI 解析签名时
  用「包装函数的 ``__globals__``」（slowapi 模块命名空间）去 eval 字符串注解，
  导致 ``UploadFile`` / ``ChatRequest`` 等 ForwardRef 解析失败而报 ``FastAPIError``。
- 改用 ``dependencies=[Depends(...)]`` 完全不触碰端点签名，规避该问题；
  限流逻辑自身在依赖里读 ``Request``，干净可测。
- ``limits`` 是 slowapi 的传递依赖（必装），直接复用其 moving-window 算法。

限流 key：优先已认证 ``request.state.user_id``（main.py 中间件在路由前写入），
匿名/静态请求回退来源 IP。这样同一用户换 IP 仍受限。

限额值来自 ``config.Settings``：
- ``rate_limit_default``：普通接口（如匿名登录）
- ``rate_limit_llm``：LLM 问答/研究（昂贵）
- ``rate_limit_ingest``：知识库入库（昂贵）
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request, status
from limits import RateLimitItem, parse
from limits.storage import storage_from_string
from limits.strategies import MovingWindowRateLimiter

if TYPE_CHECKING:
    from config import Settings


def _user_or_ip_key(request: Request) -> str:
    """限流 key：优先已认证 user_id，回退来源 IP。"""
    user_id = getattr(request.state, "user_id", None)
    if user_id:
        return f"user:{user_id}"
    client = request.client
    return f"ip:{client.host if client is not None else 'unknown'}"


class RateLimiter:
    """轻量限流器：``dependency(limit_value)`` 产出一个 FastAPI 依赖，超额抛 429。"""

    def __init__(
        self,
        *,
        key_func: Callable[[Request], str] = _user_or_ip_key,
        storage_uri: str = "memory://",
    ) -> None:
        self._key_func = key_func
        self._strategy = MovingWindowRateLimiter(storage_from_string(storage_uri))

    def dependency(self, limit_value: str) -> Callable[[Request], None]:
        """构造限流依赖；``limit_value`` 形如 ``"20/minute"``。"""
        item: RateLimitItem = parse(limit_value)

        def _check(request: Request) -> None:
            key = self._key_func(request)
            # 用 limit_value 作 namespace：不同端点的不同限额各自独立计数桶
            if self._strategy.hit(item, limit_value, key):
                return
            headers: dict[str, str] = {}
            try:
                stats = self._strategy.get_window_stats(item, limit_value, key)
                retry_after = max(0, int(stats.reset_time - time.time()))
                headers["Retry-After"] = str(retry_after)
            except Exception:  # pragma: no cover - 统计失败不应影响限流本身
                pass
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error_code": "RATE_LIMITED",
                    "message": f"请求过于频繁，请稍后重试（限额 {limit_value}）",
                },
                headers=headers or None,
            )

        return _check


def build_limiter(settings: Settings) -> RateLimiter | None:
    """构造限流器；``rate_limit_enabled=False`` 时返回 ``None``（限流退化为关闭）。"""
    if not settings.rate_limit_enabled:
        return None
    return RateLimiter()
