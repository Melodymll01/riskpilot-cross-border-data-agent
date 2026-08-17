"""记忆后台作业调度（Step 030b/c，§14.1 显式调度起步）。

``ThreadPoolMemoryScheduler`` 把 L2 摘要 / L4 固化任务丢到守护线程池，best-effort 执行，
绝不阻塞主回复、绝不让异常冒泡到请求线程。SQLite 连接池是线程局部的，
后台线程会拿到自己的连接，安全。
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from domain.ports import MemoryPort

if TYPE_CHECKING:
    from infra.memory.consolidation import ConsolidationWorker

logger = logging.getLogger(__name__)


class ThreadPoolMemoryScheduler:
    """``MemoryJobSchedulerPort`` 的线程池实现。"""

    def __init__(
        self,
        memory: MemoryPort,
        *,
        summary_threshold: int = 20,
        consolidation_worker: ConsolidationWorker | None = None,
        max_workers: int = 2,
    ) -> None:
        self._memory = memory
        self._threshold = summary_threshold
        self._consolidation_worker = consolidation_worker
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="mem-job")

    def schedule_summarization(self, owner_id: str, task_id: str) -> None:
        """提交一次 L2 摘要作业；提交本身也吞掉异常（如池已关闭）。"""
        try:
            self._pool.submit(self._run, owner_id, task_id)
        except RuntimeError:  # 池已 shutdown，best-effort 放弃
            logger.debug("记忆调度池已关闭，跳过摘要作业")

    def schedule_consolidation(self, owner_id: str, task_id: str) -> None:
        """提交一次 L4 固化作业；未配置 worker / 池已关闭均为安全空操作。"""
        if self._consolidation_worker is None:
            return
        try:
            self._pool.submit(self._run_consolidation, owner_id, task_id)
        except RuntimeError:  # 池已 shutdown，best-effort 放弃
            logger.debug("记忆调度池已关闭，跳过固化作业")

    def _run(self, owner_id: str, task_id: str) -> None:
        try:
            self._memory.maybe_summarize(owner_id, task_id, self._threshold)
        except Exception:  # noqa: BLE001 — 后台作业绝不抛出
            logger.warning("L2 摘要后台作业失败", exc_info=True)

    def _run_consolidation(self, owner_id: str, task_id: str) -> None:
        if self._consolidation_worker is None:  # pragma: no cover — 提交前已校验
            return
        try:
            self._consolidation_worker.consolidate(owner_id, task_id)
        except Exception:  # noqa: BLE001 — 后台作业绝不抛出
            logger.warning("L4 固化后台作业失败", exc_info=True)

    def shutdown(self, *, wait: bool = False) -> None:
        self._pool.shutdown(wait=wait)
