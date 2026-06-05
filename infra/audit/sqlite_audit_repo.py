"""SQLite ``AuditLogPort`` 实现（Step 021）。

职责：把 ``AuditEntry`` 写入 ``audit_log`` 表 + 按过滤条件读取。复用
``SqliteConnectionPool``（与 ``users`` / ``tasks`` 同一 db 文件，新增一张表）。

设计要点：
- ``audit_log`` 表 schema 在本模块的 ``_SCHEMA_AUDIT`` 里管理，构造时幂等
  ``executescript``；不污染 ``infra/storage/_db.py`` 的核心 schema
- ``success`` 在 SQLite 里存 ``INTEGER 0/1``（SQLite 没有原生 bool）
- ``extra_json`` 存 JSON 字符串；空 dict 落库为 ``"{}"``
- ``list_recent`` 按 ``(timestamp DESC, id DESC)`` 排序：同一 ms 内多条
  按写入顺序倒序（id 自增）
"""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING, Any

from domain.models import AuditEntry

if TYPE_CHECKING:
    from infra.storage._db import SqliteConnectionPool

# 独立 schema：与 _db.py 的核心表分离，便于 Step 021 单点维护。
# 索引覆盖最常用的"按 actor 看自己历史" / "按 action 看某类操作历史"两种查询。
_SCHEMA_AUDIT = """
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   REAL NOT NULL,
    actor_id    TEXT NOT NULL,
    action      TEXT NOT NULL,
    resource    TEXT NOT NULL,
    request_id  TEXT,
    success     INTEGER NOT NULL,
    error       TEXT,
    extra_json  TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_audit_actor
    ON audit_log(actor_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_audit_action
    ON audit_log(action, timestamp DESC);
"""


class SqliteAuditLogRepo:
    """``AuditLogPort`` 的 SQLite 实现。

    构造时幂等创建 ``audit_log`` 表 + 索引；多次构造无副作用。
    """

    def __init__(self, pool: SqliteConnectionPool) -> None:
        self._pool = pool
        conn = self._pool.get()
        conn.executescript(_SCHEMA_AUDIT)
        conn.commit()

    def record(self, entry: AuditEntry) -> None:
        conn = self._pool.get()
        conn.execute(
            """
            INSERT INTO audit_log
                (timestamp, actor_id, action, resource, request_id,
                 success, error, extra_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.timestamp,
                entry.actor_id,
                entry.action,
                entry.resource,
                entry.request_id,
                1 if entry.success else 0,
                entry.error,
                json.dumps(entry.extra_json, ensure_ascii=False),
            ),
        )
        conn.commit()

    def list_recent(
        self,
        *,
        limit: int = 50,
        action: str | None = None,
        actor_id: str | None = None,
    ) -> list[AuditEntry]:
        conn = self._pool.get()
        clauses: list[str] = []
        params: list[Any] = []
        if action is not None:
            clauses.append("action = ?")
            params.append(action)
        if actor_id is not None:
            clauses.append("actor_id = ?")
            params.append(actor_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        rows = conn.execute(
            f"""
            SELECT timestamp, actor_id, action, resource, request_id,
                   success, error, extra_json
            FROM audit_log
            {where}
            ORDER BY timestamp DESC, id DESC
            LIMIT ?
            """,  # noqa: S608  -- where 仅由白名单字段拼接，参数走 ? 占位
            params,
        ).fetchall()
        return [_row_to_entry(r) for r in rows]


def _row_to_entry(row: sqlite3.Row) -> AuditEntry:
    return AuditEntry(
        timestamp=row["timestamp"],
        actor_id=row["actor_id"],
        action=row["action"],
        resource=row["resource"],
        request_id=row["request_id"],
        success=bool(row["success"]),
        error=row["error"],
        extra_json=json.loads(row["extra_json"] or "{}"),
    )
