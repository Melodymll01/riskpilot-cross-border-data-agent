"""SQLite ``FeedbackRepoPort`` 实现：点赞/点踩反馈持久化。

一条 assistant 消息至多一条反馈（``msg_id`` 主键）；重复提交按 owner 校验后幂等上写。
"""

from __future__ import annotations

from domain.models import MessageFeedback
from infra.storage._db import SqliteConnectionPool


class SqliteFeedbackRepo:
    """``FeedbackRepoPort`` 的 SQLite 实现。"""

    def __init__(self, pool: SqliteConnectionPool) -> None:
        self._pool = pool

    def set(self, feedback: MessageFeedback) -> None:
        """写入或更新一条反馈（按 ``msg_id`` upsert）。"""
        conn = self._pool.get()
        conn.execute(
            """
            INSERT INTO message_feedback
                (msg_id, task_id, owner_id, rating, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(msg_id) DO UPDATE SET
                rating     = excluded.rating,
                updated_at = excluded.updated_at
            WHERE message_feedback.owner_id = excluded.owner_id
            """,
            (
                feedback.msg_id,
                feedback.task_id,
                feedback.owner_id,
                feedback.rating,
                feedback.created_at,
                feedback.updated_at,
            ),
        )
        conn.commit()

    def clear(self, msg_id: str, owner_id: str) -> bool:
        """撤销反馈（用户再次点击同一按钮取消）。返回是否删除了行。"""
        conn = self._pool.get()
        cur = conn.execute(
            "DELETE FROM message_feedback WHERE msg_id = ? AND owner_id = ?",
            (msg_id, owner_id),
        )
        conn.commit()
        return cur.rowcount > 0

    def get_for_task(self, task_id: str, owner_id: str) -> dict[str, str]:
        """返回该 task 下 ``{msg_id: rating}`` 映射（仅当前 owner），供前端回显按钮状态。"""
        conn = self._pool.get()
        rows = conn.execute(
            "SELECT msg_id, rating FROM message_feedback WHERE task_id = ? AND owner_id = ?",
            (task_id, owner_id),
        ).fetchall()
        return {r["msg_id"]: r["rating"] for r in rows}

    def counts(self) -> dict[str, int]:
        """全局点赞/点踩计数（后台统计）。"""
        conn = self._pool.get()
        rows = conn.execute(
            "SELECT rating, COUNT(*) AS n FROM message_feedback GROUP BY rating"
        ).fetchall()
        out = {"up": 0, "down": 0}
        for r in rows:
            out[r["rating"]] = r["n"]
        return out
