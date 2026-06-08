"""SQLite ``ProfileStorePort`` 实现：L3 用户画像存储（Step 030d）。

画像按 ``owner_id`` 单行存储，``facts`` 为 JSON 序列化的偏好字典；无 TTL，
靠主动遗忘（``delete_owner``）清除。
"""

from __future__ import annotations

import json

from domain.models import SessionProfile
from infra.storage._db import SqliteConnectionPool


class SqliteProfileStore:
    """``ProfileStorePort`` 的 SQLite 实现，挂在 ``profiles`` 表上。"""

    def __init__(self, pool: SqliteConnectionPool) -> None:
        self._pool = pool

    def get(self, owner_id: str) -> SessionProfile | None:
        conn = self._pool.get()
        row = conn.execute(
            "SELECT * FROM profiles WHERE owner_id = ?",
            (owner_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            facts = json.loads(row["facts"]) or {}
        except (json.JSONDecodeError, TypeError):
            facts = {}
        return SessionProfile(
            owner_id=row["owner_id"],
            facts=facts,
            updated_at=row["updated_at"],
        )

    def upsert(self, profile: SessionProfile) -> None:
        conn = self._pool.get()
        conn.execute(
            """
            INSERT INTO profiles (owner_id, facts, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(owner_id) DO UPDATE SET
                facts      = excluded.facts,
                updated_at = excluded.updated_at
            """,
            (
                profile.owner_id,
                json.dumps(profile.facts, ensure_ascii=False),
                profile.updated_at,
            ),
        )
        conn.commit()

    def delete_owner(self, owner_id: str) -> int:
        conn = self._pool.get()
        cur = conn.execute("DELETE FROM profiles WHERE owner_id = ?", (owner_id,))
        conn.commit()
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
