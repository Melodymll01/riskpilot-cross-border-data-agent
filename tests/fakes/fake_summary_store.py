"""``SummaryStorePort`` 内存 Fake：用于 L2 摘要测试（S-030b）。"""

from __future__ import annotations

from domain.models import TaskSummary


class InMemorySummaryStore:
    """按 task_id 存一条摘要，带 owner 归属校验。"""

    def __init__(self) -> None:
        self._data: dict[str, TaskSummary] = {}

    def get(self, task_id: str, owner_id: str) -> TaskSummary | None:
        rec = self._data.get(task_id)
        if rec is None or rec.owner_id != owner_id:
            return None
        return rec

    def upsert(self, summary: TaskSummary) -> None:
        self._data[summary.task_id] = summary
