"""SQLite ``SummaryStorePort`` 实现：L2 情景摘要存储（Step 030b）。"""

from __future__ import annotations

from domain.models import TaskSummary
from infra.storage._db import SqliteConnectionPool


class SqliteSummaryStore:
    """``SummaryStorePort`` 的 SQLite 实现，挂在 ``task_summaries`` 表上。"""

    def __init__(self, pool: SqliteConnectionPool) -> None:
        self._pool = pool

    def get(self, task_id: str, owner_id: str) -> TaskSummary | None:
        conn = self._pool.get()
        row = conn.execute(
            "SELECT * FROM task_summaries WHERE task_id = ? AND owner_id = ?",
            (task_id, owner_id),
        ).fetchone()
        if row is None:
            return None
        return TaskSummary(
            task_id=row["task_id"],
            owner_id=row["owner_id"],
            summary=row["summary"],
            msg_watermark=row["msg_watermark"],
            updated_at=row["updated_at"],
        )

    def upsert(self, summary: TaskSummary) -> None:
        conn = self._pool.get()
        conn.execute(
            """
            INSERT INTO task_summaries
                (task_id, owner_id, summary, msg_watermark, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                summary       = excluded.summary,
                msg_watermark = excluded.msg_watermark,
                updated_at    = excluded.updated_at
            """,
            (
                summary.task_id,
                summary.owner_id,
                summary.summary,
                summary.msg_watermark,
                summary.updated_at,
            ),
        )
        conn.commit()

    def delete_owner(self, owner_id: str) -> int:
        conn = self._pool.get()
        cur = conn.execute("DELETE FROM task_summaries WHERE owner_id = ?", (owner_id,))
        conn.commit()
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
