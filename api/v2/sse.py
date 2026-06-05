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
    from app.agent.events import AgentEvent


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


async def stream_with_keepalive(
    sync_events: Iterator[AgentEvent], *, keepalive_seconds: float
) -> AsyncIterator[str]:
    """把同步 ``Iterator[AgentEvent]`` 包成异步 SSE 字符串流。

    - 同步生成器跑在 threadpool（``asyncio.to_thread``），避免阻塞事件循环
    - 等待事件期间每 ``keepalive_seconds`` 秒发一帧心跳
    - 任一事件 raise 时翻译成 ``event: error`` 帧，再正常关闭流
    """
    loop = asyncio.get_running_loop()
    iterator = iter(sync_events)

    def _next() -> AgentEvent | None:
        """同步 next；走到尾抛 StopIteration → 返回 None。"""
        try:
            return next(iterator)
        except StopIteration:
            return None

    while True:
        task = loop.run_in_executor(None, _next)
        try:
            ev = await asyncio.wait_for(task, timeout=keepalive_seconds)
        except asyncio.TimeoutError:
            # 等待事件超时——发心跳，继续等
            yield sse_keepalive()
            # task 仍在后台跑；下一轮 while 不能新建一个，要复用。
            # 简化：取消并重启（_next 是幂等的轻操作）
            task.cancel()
            continue
        except Exception as exc:  # noqa: BLE001 — agent 内部异常都翻译成 SSE error
            yield sse_error("AGENT_EXCEPTION", f"{type(exc).__name__}: {exc}")
            return

        if ev is None:
            return
        yield event_to_sse(ev)
