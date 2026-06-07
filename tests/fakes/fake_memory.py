"""``MemoryPort`` 内存 Fake：用于装配 / 注入 / 降级测试（S-030a）。

只实现 L1 ``recent_messages``（带 owner 归属校验语义）+ ``append_message``；
L2/L3/L4 抛 ``NotImplementedError``，与生产适配器对齐。
"""

from __future__ import annotations

from domain.models import Fact, Message, SessionProfile


class FakeMemory:
    """可预置历史、可断言调用参数的记忆 Fake。"""

    def __init__(
        self,
        *,
        messages: dict[str, list[Message]] | None = None,
        owners: dict[str, str] | None = None,
    ) -> None:
        # task_id -> 消息列表
        self._messages: dict[str, list[Message]] = messages or {}
        # task_id -> owner_id（用于归属校验）；缺省视为任意 owner 可读。
        self._owners: dict[str, str] = owners or {}
        self.recent_calls: list[tuple[str, str, int]] = []

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

    # ── L2/L3/L4 占位 ──────────────────────────────────────────────────────

    def get_summary(self, task_id: str) -> str | None:
        raise NotImplementedError

    def maybe_summarize(self, task_id: str, threshold: int = 20) -> None:
        raise NotImplementedError

    def get_profile(self, owner_id: str) -> SessionProfile:
        raise NotImplementedError

    def update_profile(self, owner_id: str, facts: dict[str, str]) -> None:
        raise NotImplementedError

    def recall_semantic(self, owner_id: str, query: str, k: int) -> list[Fact]:
        raise NotImplementedError
