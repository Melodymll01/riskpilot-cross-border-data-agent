"""L1 短期记忆：以 ``TaskRepoPort`` 为存储后端的记忆适配器（S-030a）。

设计要点：
- L1 数据不另存一份，直接复用 task 消息表，避免双写与一致性问题。
- 读取前强制 ``owner_id`` 归属校验：非本人 task 一律返回空，
  既不抛异常（图省事降级）也不泄露跨用户数据。
- 仅实现 L1（``append_message`` / ``recent_messages``）；
  L2/L3/L4 方法保留接口但抛 ``NotImplementedError``，由后续步骤补齐。
"""

from __future__ import annotations

from domain.models import Fact, Message, SessionProfile
from domain.ports import TaskRepoPort


class TaskBackedMemory:
    """``MemoryPort`` 的 L1 实现，底层挂在 task 消息表上。"""

    def __init__(self, task_repo: TaskRepoPort) -> None:
        self._repo = task_repo

    # ── L1 短期 ────────────────────────────────────────────────────────────

    def append_message(self, task_id: str, msg: Message) -> None:
        """写入一条消息（薄委托给 task 仓储）。

        注意：当前 agent 主循环仍直接经 ``task_repo`` 落库，
        本方法保留以保证 ``MemoryPort`` L1 语义完整。
        """
        self._repo.append_message(msg)

    def recent_messages(self, owner_id: str, task_id: str, n: int) -> list[Message]:
        """返回该 task 最近 n 条消息；非本人或越界一律返回空列表。"""
        if n <= 0:
            return []
        # 归属校验：拿不到（不存在或非本人）即视为无历史，安全降级。
        if self._repo.get(task_id, owner_id) is None:
            return []
        msgs = self._repo.list_messages(task_id)
        if not msgs:
            return []
        return msgs[-n:]

    # ── L2/L3/L4：占位，后续步骤实现 ───────────────────────────────────────

    def get_summary(self, task_id: str) -> str | None:  # pragma: no cover
        raise NotImplementedError("L2 摘要将在 S-030b 实现")

    def maybe_summarize(self, task_id: str, threshold: int = 20) -> None:  # pragma: no cover
        raise NotImplementedError("L2 摘要将在 S-030b 实现")

    def get_profile(self, owner_id: str) -> SessionProfile:  # pragma: no cover
        raise NotImplementedError("L3 用户画像将在 S-030d 实现")

    def update_profile(self, owner_id: str, facts: dict[str, str]) -> None:  # pragma: no cover
        raise NotImplementedError("L3 用户画像将在 S-030d 实现")

    def recall_semantic(self, owner_id: str, query: str, k: int) -> list[Fact]:  # pragma: no cover
        raise NotImplementedError("L4 语义事实将在 S-030c 实现")
