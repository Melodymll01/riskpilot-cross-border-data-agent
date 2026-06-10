"""SQLite ``MemorySettingsStorePort`` 实现：每用户记忆开关（Step 031a）。

每个 ``owner_id`` 单行存记忆开关（``use_saved_memory``），缺省开。
布尔以 INTEGER 0/1 落盘。``get`` 缺失返回 None（调用方视为默认开）。
"""

from __future__ import annotations

from domain.models import MemorySettings
from infra.storage._db import SqliteConnectionPool


class SqliteMemorySettingsStore:
    """``MemorySettingsStorePort`` 的 SQLite 实现，挂在 ``memory_settings`` 表上。"""

    def __init__(self, pool: SqliteConnectionPool) -> None:
        self._pool = pool

    def get(self, owner_id: str) -> MemorySettings | None:
        conn = self._pool.get()
        row = conn.execute(
            "SELECT * FROM memory_settings WHERE owner_id = ?",
            (owner_id,),
        ).fetchone()
        if row is None:
            return None
        return MemorySettings(
            owner_id=row["owner_id"],
            use_saved_memory=bool(row["use_saved_memory"]),
            updated_at=row["updated_at"],
        )

    def upsert(self, settings: MemorySettings) -> None:
        conn = self._pool.get()
        conn.execute(
            """
            INSERT INTO memory_settings
                (owner_id, use_saved_memory, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(owner_id) DO UPDATE SET
                use_saved_memory  = excluded.use_saved_memory,
                updated_at        = excluded.updated_at
            """,
            (
                settings.owner_id,
                int(settings.use_saved_memory),
                settings.updated_at,
            ),
        )
        conn.commit()
