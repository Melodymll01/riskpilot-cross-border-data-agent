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
import contextvars
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import pytest

from api.v2.sse import stream_with_keepalive

# ──────────────────────────── 测试用最小 AgentEvent stub ────────────────────────────


class _FakeEventType(StrEnum):
    THOUGHT = "thought"
    ANSWER = "answer"


@dataclass
class _FakeEvent:
    event_type: _FakeEventType
    payload: dict[str, Any]


class _LifecycleEvents:
    def __init__(self) -> None:
        self._events = iter([_FakeEvent(_FakeEventType.THOUGHT, {"text": "started"})])
        self.disconnect_count = 0
        self.close_count = 0

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._events)

    def disconnect(self) -> None:
        self.disconnect_count += 1

    def close(self) -> None:
        self.close_count += 1


class _BlockingCancelableEvents:
    def __init__(self) -> None:
        self.index = 0
        self.waiting = threading.Event()
        self.release = threading.Event()
        self.disconnect_count = 0
        self.close_count = 0

    def __iter__(self):
        return self

    def __next__(self):
        if self.index == 0:
            self.index += 1
            return _FakeEvent(_FakeEventType.THOUGHT, {"text": "started"})
        self.waiting.set()
        self.release.wait(timeout=2.0)
        raise StopIteration

    def disconnect(self) -> None:
        self.disconnect_count += 1
        self.release.set()

    def close(self) -> None:
        self.close_count += 1


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
    def test_generator_context_survives_yields_and_disconnect_cleanup(self) -> None:
        marker = contextvars.ContextVar("sse_context_marker", default="outside")
        waiting, release, closed = threading.Event(), threading.Event(), threading.Event()
        errors = []

        class Source:
            def __init__(self):
                self.iterator = self.events()

            def events(self):
                assert marker.get() == "request"
                token = marker.set("inside-generator")
                try:
                    yield _FakeEvent(_FakeEventType.THOUGHT, {"text": "started"})
                    assert marker.get() == "inside-generator"
                    waiting.set()
                    release.wait(timeout=2)
                    yield _FakeEvent(_FakeEventType.ANSWER, {"text": "late"})
                finally:
                    marker.reset(token)
                    closed.set()

            def __iter__(self):
                return self.iterator

            def disconnect(self):
                release.set()

            def close(self):
                self.iterator.close()

        async def run():
            loop = asyncio.get_running_loop()
            loop.set_exception_handler(lambda loop, context: errors.append(context))
            token = marker.set("request")
            source = Source()

            async def consume():
                async for _ in stream_with_keepalive(source, keepalive_seconds=0.01):
                    pass

            consumer = asyncio.create_task(consume())
            try:
                for _ in range(100):
                    if waiting.is_set():
                        break
                    await asyncio.sleep(0.01)
                assert waiting.is_set()
                consumer.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await consumer
                for _ in range(100):
                    if closed.is_set():
                        break
                    await asyncio.sleep(0.01)
                assert closed.is_set()
                assert marker.get() == "request"
                assert errors == []
            finally:
                release.set()
                marker.reset(token)

        asyncio.run(run())

    def test_fast_events_pass_through(self) -> None:
        events = [
            _FakeEvent(_FakeEventType.THOUGHT, {"text": "t1"}),
            _FakeEvent(_FakeEventType.ANSWER, {"text": "ok"}),
        ]
        frames = _collect(stream_with_keepalive(iter(events), keepalive_seconds=0.5))
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

        frames = _collect(stream_with_keepalive(slow_gen(), keepalive_seconds=0.1))

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
        frames = _collect(stream_with_keepalive(iter([]), keepalive_seconds=0.5))
        assert frames == []

    def test_normal_eof_releases_source_without_disconnect_notification(self) -> None:
        source = _LifecycleEvents()

        frames = _collect(stream_with_keepalive(source, keepalive_seconds=0.5))

        assert len(frames) == 1
        assert source.disconnect_count == 0
        assert source.close_count == 1

    def test_generator_exception_becomes_sse_error_frame(self) -> None:
        def bad_gen() -> Iterator[_FakeEvent]:
            yield _FakeEvent(_FakeEventType.THOUGHT, {"text": "before"})
            raise RuntimeError("boom")

        frames = _collect(stream_with_keepalive(bad_gen(), keepalive_seconds=0.5))
        assert len(frames) == 2
        assert "event: thought" in frames[0]
        assert "event: error" in frames[1]
        assert "RuntimeError" in frames[1]
        assert "boom" not in frames[1]

    def test_consumer_disconnect_invokes_backend_cancel_hook(self) -> None:
        source = _LifecycleEvents()

        async def consume_one_then_disconnect() -> None:
            stream = stream_with_keepalive(source, keepalive_seconds=0.5)
            frame = await anext(stream)
            assert "event: thought" in frame
            await stream.aclose()

        asyncio.run(consume_one_then_disconnect())

        assert source.disconnect_count == 1
        assert source.close_count == 1

    def test_disconnect_while_sync_next_is_running_signals_then_closes(self) -> None:
        source = _BlockingCancelableEvents()

        async def disconnect_during_next() -> None:
            async def consume() -> None:
                async for _frame in stream_with_keepalive(source, keepalive_seconds=0.05):
                    pass

            consumer = asyncio.create_task(consume())
            while not source.waiting.is_set():
                await asyncio.sleep(0.01)
            consumer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consumer
            for _ in range(50):
                if source.close_count:
                    return
                await asyncio.sleep(0.01)

        asyncio.run(disconnect_during_next())

        assert source.disconnect_count == 1
        assert source.close_count == 1
