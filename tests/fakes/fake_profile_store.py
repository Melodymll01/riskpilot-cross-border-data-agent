"""``ProfileStorePort`` 内存 Fake：用于 L3 画像测试（S-030d）。"""

from __future__ import annotations

from domain.models import SessionProfile


class InMemoryProfileStore:
    """按 owner_id 存一条画像。"""

    def __init__(self) -> None:
        self._data: dict[str, SessionProfile] = {}

    def get(self, owner_id: str) -> SessionProfile | None:
        return self._data.get(owner_id)

    def upsert(self, profile: SessionProfile) -> None:
        self._data[profile.owner_id] = profile

    def delete_owner(self, owner_id: str) -> int:
        return 1 if self._data.pop(owner_id, None) is not None else 0
