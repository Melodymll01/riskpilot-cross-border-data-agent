"""``MemorySettingsStorePort`` 内存 Fake：用于记忆开关测试（S-031a）。"""

from __future__ import annotations

from domain.models import MemorySettings


class InMemoryMemorySettingsStore:
    """按 owner_id 存一条记忆开关。"""

    def __init__(self) -> None:
        self._data: dict[str, MemorySettings] = {}

    def get(self, owner_id: str) -> MemorySettings | None:
        return self._data.get(owner_id)

    def upsert(self, settings: MemorySettings) -> None:
        self._data[settings.owner_id] = settings
