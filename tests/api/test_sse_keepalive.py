"""``api.v2.sse.stream_with_keepalive`` 单元测试。

重点防回归：**慢同步生成器场景下不能触发**
``ValueError: generator already executing``。

历史 bug：旧实现在 keepalive 超时时 ``task.cancel()`` 后下一轮 while
``loop.run_in_executor(None, _next)`` 又开一个线程跑 ``next(iterator)``，
导致同一个 generator 被并发驱动；LLM 第一个 token > 15s 时必触发。
修复策略：超时只发心跳，task 跨循环复用，不 cancel。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum
from typing import Any

import pytest

from api.v2.sse import stream_with_keepalive


# ──────────────────────────── 测试用最小 AgentEvent stub ────────────────────────────


class _FakeEventType(str, Enum):
    THOUGHT = "thought"
    ANSWER = "answer"


@dataclass
class _FakeEvent:
    event_type: _FakeEventType
    payload: dict[str, Any]


def _collect(coro_async_iter):
    """把 async iterator 收集成 list（同步）。"""

    async def _run():
        out = []
        async for frame in coro_async_iter:
            out.append(frame)
        return out

    return asyncio.run(_run())


# ──────────────────────────── 用例 ────────────────────────────


class TestStreamWithKeepalive:
    def test_fast_events_pass_through(self) -> None:
        events = [
            _FakeEvent(_FakeEventType.THOUGHT, {"text": "t1"}),
            _FakeEvent(_FakeEventType.ANSWER, {"text": "ok"}),
        ]
        frames = _collect(
            stream_with_keepalive(iter(events), keepalive_seconds=0.5)
        )
        # 两个事件帧，无心跳
        event_frames = [f for f in frames if not f.startswith(":")]
        assert len(event_frames) == 2
        assert "event: thought" in event_frames[0]
        assert "event: answer" in event_frames[1]

    def test_slow_generator_emits_keepalive_without_concurrency_error(self) -> None:
        """关键防回归：慢 generator + 短 keepalive 不能触发 generator already executing。"""

        def slow_gen() -> Iterator[_FakeEvent]:
            time.sleep(0.25)  # > keepalive_seconds，触发至少一次心跳
            yield _FakeEvent(_FakeEventType.THOUGHT, {"text": "slow-1"})
            time.sleep(0.25)
            yield _FakeEvent(_FakeEventType.ANSWER, {"text": "done"})

        frames = _collect(
            stream_with_keepalive(slow_gen(), keepalive_seconds=0.1)
        )

        keepalive_frames = [f for f in frames if f.startswith(":")]
        event_frames = [f for f in frames if not f.startswith(":")]

        # 至少出现一次心跳（每个慢事件期间）
        assert len(keepalive_frames) >= 2, f"心跳帧数 {len(keepalive_frames)}, 全部帧={frames}"
        # 两个事件正常通过
        assert len(event_frames) == 2
        assert "event: thought" in event_frames[0]
        assert "event: answer" in event_frames[1]
        # 没有 error 帧（关键：旧实现这里会抛 generator already executing → SSE error）
        assert not any("event: error" in f for f in frames), f"出现 error 帧：{frames}"

    def test_empty_generator_returns_immediately(self) -> None:
        frames = _collect(
            stream_with_keepalive(iter([]), keepalive_seconds=0.5)
        )
        assert frames == []

    def test_generator_exception_becomes_sse_error_frame(self) -> None:
        def bad_gen() -> Iterator[_FakeEvent]:
            yield _FakeEvent(_FakeEventType.THOUGHT, {"text": "before"})
            raise RuntimeError("boom")

        frames = _collect(
            stream_with_keepalive(bad_gen(), keepalive_seconds=0.5)
        )
        assert len(frames) == 2
        assert "event: thought" in frames[0]
        assert "event: error" in frames[1]
        assert "RuntimeError" in frames[1]
        assert "boom" in frames[1]
