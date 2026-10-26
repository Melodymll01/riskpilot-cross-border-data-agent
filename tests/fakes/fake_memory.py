"""``MemoryPort`` 内存 Fake：用于装配 / 注入 / 降级测试（S-030a/d）。"""

from __future__ import annotations

from domain.models import Message


class FakeMemory:
    """可预置历史、可断言调用参数的记忆 Fake。"""

    def __init__(self, *, messages=None, summaries=None, owners=None) -> None:
        self._owners: dict[str, str] = owners or {}
        self._messages: dict[str, list[Message]] = messages or {}
        self._summaries: dict[str, str] = summaries or {}
        self.recent_calls: list[tuple] = []
        self.summary_calls: list[tuple] = []
        self.summarize_calls: list[tuple] = []

    def recent_messages(self, owner_id: str, task_id: str, n: int) -> list[Message]:
        self.recent_calls.append((owner_id, task_id, n))
        if n <= 0:
            return []
        expected = self._owners.get(task_id)
        if expected is not None and expected != owner_id:
            return []  # 归属不符：安全降级，不泄露
        msgs = self._messages.get(task_id, [])
        return msgs[-n:]

    # ── L2 摘要 ────────────────────────────────────────────────────────────

    def get_summary(self, owner_id: str, task_id: str) -> str | None:
        expected = self._owners.get(task_id)
        if expected is not None and expected != owner_id:
            return None
        return self._summaries.get(task_id)

    def maybe_summarize(self, owner_id: str, task_id: str, threshold: int = 20) -> None:
        self.summarize_calls.append((owner_id, task_id, threshold))

    def seed(self, task_id: str, messages: list[Message], *, owner_id: str | None = None) -> None:
        self._messages[task_id] = messages
        if owner_id is not None:
            self._owners[task_id] = owner_id

    def seed_summary(self, task_id: str, summary: str) -> None:
        self._summaries[task_id] = summary
