"""ThreadPoolMemoryScheduler 测试（S-030b）。"""

from __future__ import annotations

import threading

import pytest

from infra.memory import ThreadPoolMemoryScheduler

pytestmark = pytest.mark.unit


class _RecordingMemory:
    """记录 maybe_summarize 调用；用事件等待后台线程完成。"""

    def __init__(self, *, boom: bool = False) -> None:
        self.calls: list[tuple[str, str, int]] = []
        self._boom = boom
        self.done = threading.Event()

    def maybe_summarize(self, owner_id: str, task_id: str, threshold: int = 20) -> None:
        try:
            if self._boom:
                raise RuntimeError("故意炸")
            self.calls.append((owner_id, task_id, threshold))
        finally:
            self.done.set()

    # 其余 MemoryPort 方法本测试用不到
    def recent_messages(self, *a: object, **k: object) -> list:  # pragma: no cover
        return []

    def get_summary(self, *a: object, **k: object) -> None:  # pragma: no cover
        return None


class TestSchedule:
    def test_submits_and_runs_maybe_summarize(self) -> None:
        mem = _RecordingMemory()
        sched = ThreadPoolMemoryScheduler(mem, summary_threshold=5)  # type: ignore[arg-type]

        sched.schedule_summarization("anon:o1", "t1")
        sched.shutdown(wait=True)

        assert mem.calls == [("anon:o1", "t1", 5)]

    def test_background_error_is_swallowed(self) -> None:
        mem = _RecordingMemory(boom=True)
        sched = ThreadPoolMemoryScheduler(mem)  # type: ignore[arg-type]

        sched.schedule_summarization("anon:o1", "t1")
        sched.shutdown(wait=True)  # 不应抛

        assert mem.done.is_set()
        assert mem.calls == []

    def test_schedule_after_shutdown_is_noop(self) -> None:
        mem = _RecordingMemory()
        sched = ThreadPoolMemoryScheduler(mem)  # type: ignore[arg-type]
        sched.shutdown(wait=True)

        sched.schedule_summarization("anon:o1", "t1")  # 池已关闭，不抛

        assert mem.calls == []


class _RecordingWorker:
    """记录 consolidate 调用；用事件等待后台线程完成。"""

    def __init__(self, *, boom: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self._boom = boom
        self.done = threading.Event()

    def consolidate(self, owner_id: str, task_id: str) -> None:
        try:
            if self._boom:
                raise RuntimeError("故意炸")
            self.calls.append((owner_id, task_id))
        finally:
            self.done.set()


class TestScheduleConsolidation:
    def test_submits_and_runs_consolidate(self) -> None:
        mem = _RecordingMemory()
        worker = _RecordingWorker()
        sched = ThreadPoolMemoryScheduler(
            mem,  # type: ignore[arg-type]
            consolidation_worker=worker,  # type: ignore[arg-type]
        )

        sched.schedule_consolidation("anon:o1", "t1")
        sched.shutdown(wait=True)

        assert worker.calls == [("anon:o1", "t1")]

    def test_noop_without_worker(self) -> None:
        mem = _RecordingMemory()
        sched = ThreadPoolMemoryScheduler(mem)  # type: ignore[arg-type]

        sched.schedule_consolidation("anon:o1", "t1")  # 无 worker，不抛
        sched.shutdown(wait=True)

    def test_background_error_is_swallowed(self) -> None:
        mem = _RecordingMemory()
        worker = _RecordingWorker(boom=True)
        sched = ThreadPoolMemoryScheduler(
            mem,  # type: ignore[arg-type]
            consolidation_worker=worker,  # type: ignore[arg-type]
        )

        sched.schedule_consolidation("anon:o1", "t1")
        sched.shutdown(wait=True)  # 不应抛

        assert worker.done.is_set()
        assert worker.calls == []
