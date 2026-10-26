"""Conversation support: owner-scoped data and bounded context."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from domain.ports import MemoryPort

logger = logging.getLogger(__name__)


class ThreadPoolMemoryScheduler:
    """``MemoryJobSchedulerPort`` 的线程池实现。"""

    def __init__(
        self,
        memory: MemoryPort,
        *,
        summary_threshold: int = 20,
        max_workers: int = 2,
    ) -> None:
        self._memory = memory
        self._threshold = summary_threshold
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="mem-job")

    def schedule_summarization(self, owner_id: str, task_id: str) -> None:
        """提交一次 L2 摘要作业；提交本身也吞掉异常（如池已关闭）。"""
        try:
            self._pool.submit(self._run, owner_id, task_id)
        except RuntimeError:  # 池已 shutdown，best-effort 放弃
            logger.debug("记忆调度池已关闭，跳过摘要作业")

    def _run(self, owner_id: str, task_id: str) -> None:
        try:
            self._memory.maybe_summarize(owner_id, task_id, self._threshold)
        except Exception:  # noqa: BLE001 — 后台作业绝不抛出
            logger.warning("L2 摘要后台作业失败", exc_info=True)

    def shutdown(self, *, wait: bool = False) -> None:
        self._pool.shutdown(wait=wait)
