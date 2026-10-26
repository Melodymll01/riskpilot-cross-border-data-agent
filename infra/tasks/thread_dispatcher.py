"""Bounded local execution of persisted document processing jobs."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.workers import DocumentPipelineWorker
logger = logging.getLogger(__name__)


class ThreadDocumentDispatcher:
    def __init__(self, worker: DocumentPipelineWorker, *, max_workers: int = 2) -> None:
        self._worker = worker
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="document")
        self._pending: dict[str, Future] = {}
        self._lock = threading.RLock()

    def enqueue_document(self, job_id: str, *, attempt: int) -> str:
        with self._lock:
            if job_id not in self._pending:
                if len(self._pending) >= 32:
                    raise RuntimeError("文档处理队列已满，请稍后重试")
                future = self._pool.submit(self._worker.run, job_id)
                self._pending[job_id] = future
                future.add_done_callback(lambda result: self._finished(job_id, result))
        return f"document:{job_id}:{attempt}"

    def _finished(self, job_id: str, result: Future) -> None:
        with self._lock:
            self._pending.pop(job_id, None)
        if not result.cancelled() and result.exception() is not None:
            logger.error("Document worker failed: %s", type(result.exception()).__name__)

    def cancel_document(self, job_id: str, *, attempt: int) -> None:
        with self._lock:
            future = self._pending.get(job_id)
            if future is not None:
                future.cancel()

    def shutdown(self, *, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait)
