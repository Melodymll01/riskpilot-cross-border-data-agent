"""SQLite ``ConsolidationStatePort`` 实现：L4 固化进度水位（Step 030c）。"""

from __future__ import annotations

from domain.models import ConsolidationState
from infra.storage._db import SqliteConnectionPool


class SqliteConsolidationStateStore:
    """``ConsolidationStatePort`` 的 SQLite 实现，挂在 ``consolidation_state`` 表上。"""

    def __init__(self, pool: SqliteConnectionPool) -> None:
        self._pool = pool

    def get(self, task_id: str, owner_id: str) -> ConsolidationState | None:
        conn = self._pool.get()
        row = conn.execute(
            "SELECT * FROM consolidation_state WHERE task_id = ? AND owner_id = ?",
            (task_id, owner_id),
        ).fetchone()
        if row is None:
            return None
        return ConsolidationState(
            task_id=row["task_id"],
            owner_id=row["owner_id"],
            msg_watermark=row["msg_watermark"],
            updated_at=row["updated_at"],
        )

    def upsert(self, state: ConsolidationState) -> None:
        conn = self._pool.get()
        conn.execute(
            """
            INSERT INTO consolidation_state
                (task_id, owner_id, msg_watermark, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                msg_watermark = excluded.msg_watermark,
                updated_at    = excluded.updated_at
            """,
            (
                state.task_id,
                state.owner_id,
                state.msg_watermark,
                state.updated_at,
            ),
        )
        conn.commit()

    def delete_owner(self, owner_id: str) -> int:
        conn = self._pool.get()
        cur = conn.execute(
            "DELETE FROM consolidation_state WHERE owner_id = ?", (owner_id,)
        )
        conn.commit()
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
