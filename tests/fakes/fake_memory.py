"""``MemoryPort`` 内存 Fake：用于装配 / 注入 / 降级测试（S-030a/d）。

实现 L1 ``recent_messages`` + L2 ``get_summary`` + L3 画像 + L4 ``recall_semantic``
+ 主动遗忘 ``forget``，可预置数据并断言调用参数。
"""

from __future__ import annotations

from domain.models import Fact, ForgetResult, Message, SessionProfile, TaskSummary


class FakeMemory:
    """可预置历史、可断言调用参数的记忆 Fake。"""

    def __init__(
        self,
        *,
        messages: dict[str, list[Message]] | None = None,
        owners: dict[str, str] | None = None,
        summaries: dict[str, str] | None = None,
        facts: dict[str, list[Fact]] | None = None,
        profiles: dict[str, SessionProfile] | None = None,
    ) -> None:
        # task_id -> 消息列表
        self._messages: dict[str, list[Message]] = messages or {}
        # task_id -> owner_id（用于归属校验）；缺省视为任意 owner 可读。
        self._owners: dict[str, str] = owners or {}
        # task_id -> 摘要文本（L2）
        self._summaries: dict[str, str] = summaries or {}
        # owner_id -> L4 事实列表（recall_semantic 返回值）
        self._facts: dict[str, list[Fact]] = facts or {}
        # owner_id -> L3 画像（get_profile 返回值）
        self._profiles: dict[str, SessionProfile] = profiles or {}
        self.recent_calls: list[tuple[str, str, int]] = []
        self.summarize_calls: list[tuple[str, str, int]] = []
        self.recall_calls: list[tuple[str, str, int]] = []
        self.history_calls: list[tuple[str, str, int]] = []
        self.profile_updates: list[tuple[str, dict[str, str]]] = []
        self.forget_calls: list[tuple[str, str]] = []
        self.list_facts_calls: list[str] = []

    def append_message(self, task_id: str, msg: Message) -> None:
        self._messages.setdefault(task_id, []).append(msg)

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

    def maybe_summarize(
        self, owner_id: str, task_id: str, threshold: int = 20
    ) -> None:
        self.summarize_calls.append((owner_id, task_id, threshold))

    def recall_history(
        self, owner_id: str, exclude_task_id: str, k: int
    ) -> list[TaskSummary]:
        self.history_calls.append((owner_id, exclude_task_id, k))
        if k <= 0:
            return []
        out: list[TaskSummary] = []
        for task_id, summary in self._summaries.items():
            if task_id == exclude_task_id or not summary:
                continue
            owner = self._owners.get(task_id)
            if owner is not None and owner != owner_id:
                continue
            out.append(
                TaskSummary(task_id=task_id, owner_id=owner_id, summary=summary)
            )
            if len(out) >= k:
                break
        return out

    # ── L3/L4 ──────────────────────────────────────────────────────────────

    def get_profile(self, owner_id: str) -> SessionProfile:
        return self._profiles.get(owner_id) or SessionProfile(owner_id=owner_id, facts={})

    def update_profile(self, owner_id: str, facts: dict[str, str]) -> None:
        self.profile_updates.append((owner_id, dict(facts)))
        existing = self._profiles.get(owner_id)
        merged: dict = dict(existing.facts) if existing else {}
        merged.update(facts)
        self._profiles[owner_id] = SessionProfile(owner_id=owner_id, facts=merged)

    def recall_semantic(self, owner_id: str, query: str, k: int) -> list[Fact]:
        self.recall_calls.append((owner_id, query, k))
        if k <= 0:
            return []
        return self._facts.get(owner_id, [])[:k]

    def list_facts(self, owner_id: str) -> list[Fact]:
        self.list_facts_calls.append(owner_id)
        return [f for f in self._facts.get(owner_id, []) if f.superseded_by is None]

    # ── 主动遗忘 ────────────────────────────────────────────────────────────

    def forget(self, owner_id: str, *, scope: str = "memory") -> ForgetResult:
        self.forget_calls.append((owner_id, scope))
        facts = self._facts.pop(owner_id, [])
        summaries = [t for t, o in self._owners.items() if o == owner_id]
        profile = self._profiles.pop(owner_id, None)
        return ForgetResult(
            owner_id=owner_id,
            scope=scope,
            summaries_deleted=len(summaries),
            profile_deleted=1 if profile is not None else 0,
            facts_deleted=len(facts),
        )
