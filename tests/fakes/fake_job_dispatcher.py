"""BackgroundJobDispatcherPort Fake。"""

from __future__ import annotations


class FakeJobDispatcher:
    def __init__(self, *, raise_on_enqueue: Exception | None = None) -> None:
        self.raise_on_enqueue = raise_on_enqueue
        self.enqueued: list[tuple[str, int]] = []
        self.cancelled: list[tuple[str, int]] = []

    def enqueue_document(self, job_id: str, *, attempt: int) -> str:
        if self.raise_on_enqueue is not None:
            raise self.raise_on_enqueue
        self.enqueued.append((job_id, attempt))
        return f"fake:{job_id}:attempt{attempt}"

    def cancel_document(self, job_id: str, *, attempt: int) -> None:
        self.cancelled.append((job_id, attempt))
