"""SQLite TaskRepoPort 实现。"""

from __future__ import annotations

import json
from typing import Any

from domain.models import (
    Artifact,
    Citation,
    Message,
    MessageRole,
    Task,
    TaskMode,
    TaskState,
    ToolCall,
    ToolCallStatus,
)
from infra.storage._db import SqliteConnectionPool


class SqliteTaskRepo:
    """`TaskRepoPort` 的 SQLite 实现。"""

    def __init__(self, pool: SqliteConnectionPool) -> None:
        self._pool = pool

    # ── Task ─────────────────────────────────────────────────────────────

    def create(self, task: Task) -> None:
        conn = self._pool.get()
        conn.execute(
            """
            INSERT INTO tasks
                (task_id, owner_id, title, state, mode, user_goal,
                 collected_facts, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.task_id,
                task.owner_id,
                task.title,
                task.state,
                task.mode,
                task.user_goal,
                json.dumps(task.collected_facts, ensure_ascii=False),
                task.created_at,
                task.updated_at,
            ),
        )
        conn.commit()

    def get(self, task_id: str, owner_id: str) -> Task | None:
        conn = self._pool.get()
        row = conn.execute(
            "SELECT * FROM tasks WHERE task_id = ? AND owner_id = ?",
            (task_id, owner_id),
        ).fetchone()
        if row is None:
            return None
        return _row_to_task(row)

    def list_for_owner(self, owner_id: str, limit: int = 50) -> list[Task]:
        conn = self._pool.get()
        rows = conn.execute(
            "SELECT * FROM tasks WHERE owner_id = ? "
            "ORDER BY updated_at DESC LIMIT ?",
            (owner_id, limit),
        ).fetchall()
        return [_row_to_task(r) for r in rows]

    def update(self, task: Task) -> None:
        conn = self._pool.get()
        conn.execute(
            """
            UPDATE tasks SET
                title           = ?,
                state           = ?,
                mode            = ?,
                user_goal       = ?,
                collected_facts = ?,
                updated_at      = ?
            WHERE task_id = ? AND owner_id = ?
            """,
            (
                task.title,
                task.state,
                task.mode,
                task.user_goal,
                json.dumps(task.collected_facts, ensure_ascii=False),
                task.updated_at,
                task.task_id,
                task.owner_id,
            ),
        )
        conn.commit()

    def delete(self, task_id: str, owner_id: str) -> bool:
        conn = self._pool.get()
        cur = conn.execute(
            "DELETE FROM tasks WHERE task_id = ? AND owner_id = ?",
            (task_id, owner_id),
        )
        conn.commit()
        return cur.rowcount > 0

    # ── Message ──────────────────────────────────────────────────────────

    def append_message(self, msg: Message) -> None:
        conn = self._pool.get()
        citations_json = json.dumps(
            [c.model_dump() for c in msg.citations], ensure_ascii=False
        )
        conn.execute(
            """
            INSERT INTO messages
                (msg_id, task_id, role, content, tool_call_id, citations, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                msg.msg_id,
                msg.task_id,
                msg.role,
                msg.content,
                msg.tool_call_id,
                citations_json,
                msg.created_at,
            ),
        )
        # 维护 task.updated_at —— 与 message 时间戳同步
        conn.execute(
            "UPDATE tasks SET updated_at = ? WHERE task_id = ?",
            (msg.created_at, msg.task_id),
        )
        conn.commit()

    def list_messages(self, task_id: str) -> list[Message]:
        conn = self._pool.get()
        rows = conn.execute(
            "SELECT * FROM messages WHERE task_id = ? ORDER BY created_at",
            (task_id,),
        ).fetchall()
        return [_row_to_message(r) for r in rows]

    # ── ToolCall / Artifact ──────────────────────────────────────────────

    def append_tool_call(self, call: ToolCall) -> None:
        conn = self._pool.get()
        conn.execute(
            """
            INSERT INTO tool_calls
                (tool_call_id, task_id, tool_name, input_json,
                 output_json, status, duration_ms, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tool_call_id) DO UPDATE SET
                output_json = excluded.output_json,
                status      = excluded.status,
                duration_ms = excluded.duration_ms
            """,
            (
                call.tool_call_id,
                call.task_id,
                call.tool_name,
                json.dumps(call.input_json, ensure_ascii=False),
                json.dumps(call.output_json, ensure_ascii=False)
                if call.output_json is not None
                else None,
                call.status,
                call.duration_ms,
                call.created_at,
            ),
        )
        conn.commit()

    def append_artifact(self, art: Artifact) -> None:
        conn = self._pool.get()
        conn.execute(
            """
            INSERT INTO artifacts
                (artifact_id, task_id, artifact_type, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                art.artifact_id,
                art.task_id,
                art.artifact_type,
                json.dumps(art.payload_json, ensure_ascii=False),
                art.created_at,
            ),
        )
        conn.commit()


# ── Row → Domain Model ──────────────────────────────────────────────────


def _row_to_task(row: Any) -> Task:
    # mode 在老库迁移后不会为 NULL（DEFAULT 'qa'）；充作防御。
    raw_mode = row["mode"] if "mode" in row.keys() else "qa"
    mode = _validate_mode(raw_mode or "qa")
    return Task(
        task_id=row["task_id"],
        owner_id=row["owner_id"],
        title=row["title"],
        state=_validate_state(row["state"]),
        mode=mode,
        user_goal=row["user_goal"],
        collected_facts=json.loads(row["collected_facts"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_message(row: Any) -> Message:
    citations_raw = json.loads(row["citations"])
    return Message(
        msg_id=row["msg_id"],
        task_id=row["task_id"],
        role=_validate_role(row["role"]),
        content=row["content"],
        tool_call_id=row["tool_call_id"],
        citations=[Citation(**c) for c in citations_raw],
        created_at=row["created_at"],
    )


def _validate_state(value: str) -> TaskState:
    if value not in ("planning", "gathering", "evaluating", "answering", "done"):
        msg = f"invalid task state in DB: {value!r}"
        raise ValueError(msg)
    return value  # type: ignore[return-value]


def _validate_mode(value: str) -> TaskMode:
    if value not in ("qa", "research", "profile"):
        msg = f"invalid task mode in DB: {value!r}"
        raise ValueError(msg)
    return value  # type: ignore[return-value]


def _validate_role(value: str) -> MessageRole:
    if value not in ("user", "assistant", "tool", "system"):
        msg = f"invalid message role in DB: {value!r}"
        raise ValueError(msg)
    return value  # type: ignore[return-value]


def _validate_status(value: str) -> ToolCallStatus:  # pragma: no cover - reserved
    if value not in ("pending", "success", "failed", "timeout"):
        msg = f"invalid tool call status in DB: {value!r}"
        raise ValueError(msg)
    return value  # type: ignore[return-value]
