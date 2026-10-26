"""SQLite persistence for resumable conversation runs."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from domain.errors import ConversationRunConflict
from domain.models import ConversationRun, Message
from infra.storage._db import SqliteConnectionPool


class SqliteConversationRunRepo:
    def __init__(self, pool: SqliteConnectionPool) -> None:
        self._pool = pool

    def create(self, run: ConversationRun) -> None:
        self._pool.get().execute(
            """
            INSERT INTO conversation_runs
                (run_id, thread_id, owner_id, task_id, question, input_message_id,
                 attachment_doc_ids, status, stage, evidence_json,
                 verified_citation_ids, verified_context_message_ids, clarification_fields,
                 max_duration_seconds, remaining_seconds, deadline_at,
                 max_model_calls, max_tool_calls, model_call_count, tool_call_count,
                 model_retry_count, tool_retry_count, input_tokens, output_tokens,
                 usage_reported_model_calls, answer_message_id, error_type, end_reason,
                 cancel_requested_at, finished_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _values(run),
        )
        self._pool.get().commit()

    def get(self, run_id: str, owner_id: str) -> ConversationRun | None:
        row = (
            self._pool.get()
            .execute(
                "SELECT * FROM conversation_runs WHERE run_id = ? AND owner_id = ?",
                (run_id, owner_id),
            )
            .fetchone()
        )
        return None if row is None else _row_to_run(row)

    def update(self, run: ConversationRun, *, expected_status: str | None = None) -> None:
        sql = """
            UPDATE conversation_runs SET
                status = ?, stage = ?, evidence_json = ?, verified_citation_ids = ?,
                verified_context_message_ids = ?, clarification_fields = ?,
                max_duration_seconds = ?, remaining_seconds = ?, deadline_at = ?,
                max_model_calls = ?, max_tool_calls = ?, model_call_count = ?,
                tool_call_count = ?, model_retry_count = ?, tool_retry_count = ?,
                input_tokens = ?, output_tokens = ?, usage_reported_model_calls = ?,
                answer_message_id = ?, error_type = ?, end_reason = ?,
                cancel_requested_at = ?, finished_at = ?, updated_at = ?
            WHERE run_id = ? AND owner_id = ?
            """
        values: tuple[object, ...] = (
            run.status,
            run.stage,
            json.dumps(run.evidence, ensure_ascii=False),
            json.dumps(run.verified_citation_ids, ensure_ascii=False),
            json.dumps(run.verified_context_message_ids, ensure_ascii=False),
            json.dumps(run.clarification_fields, ensure_ascii=False),
            run.max_duration_seconds,
            run.remaining_seconds,
            run.deadline_at,
            run.max_model_calls,
            run.max_tool_calls,
            run.model_call_count,
            run.tool_call_count,
            run.model_retry_count,
            run.tool_retry_count,
            run.input_tokens,
            run.output_tokens,
            run.usage_reported_model_calls,
            run.answer_message_id,
            run.error_type,
            run.end_reason,
            run.cancel_requested_at,
            run.finished_at,
            run.updated_at,
            run.run_id,
            run.owner_id,
        )
        if expected_status is not None:
            sql += " AND status = ?"
            values = (*values, expected_status)
        cursor = self._pool.get().execute(
            sql,
            values,
        )
        self._pool.get().commit()
        if cursor.rowcount != 1:
            raise ConversationRunConflict(run.run_id)

    def claim_resume(
        self,
        run_id: str,
        owner_id: str,
        *,
        updated_at: float,
    ) -> ConversationRun | None:
        conn = self._pool.get()
        cursor = conn.execute(
            """
            UPDATE conversation_runs
            SET status = 'running', stage = 'agent', error_type = NULL,
                end_reason = NULL, deadline_at = ? + remaining_seconds, updated_at = ?
            WHERE run_id = ? AND owner_id = ? AND status = 'interrupted'
              AND answer_message_id IS NULL AND remaining_seconds > 0
            """,
            (updated_at, updated_at, run_id, owner_id),
        )
        conn.commit()
        if cursor.rowcount != 1:
            return None
        return self.get(run_id, owner_id)

    def commit_terminal(
        self,
        run: ConversationRun,
        message: Message,
        *,
        validate_context: Callable[[], None] | None = None,
    ) -> None:
        if run.status not in {"completed", "failed"}:
            raise ValueError("terminal run 状态必须是 completed 或 failed")
        if run.answer_message_id != message.msg_id or run.task_id != message.task_id:
            raise ValueError("terminal run 与 assistant message 不匹配")
        if message.role != "assistant":
            raise ValueError("terminal message 必须是 assistant 消息")

        conn = self._pool.get()
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                if validate_context is not None:
                    validate_context()
                conn.execute(
                    """
                    INSERT INTO messages
                        (msg_id, task_id, role, content, tool_call_id, citations, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message.msg_id,
                        message.task_id,
                        message.role,
                        message.content,
                        message.tool_call_id,
                        json.dumps(
                            [citation.model_dump() for citation in message.citations],
                            ensure_ascii=False,
                        ),
                        message.created_at,
                    ),
                )
                cursor = conn.execute(
                    """
                    UPDATE conversation_runs SET
                        status = ?, stage = ?, evidence_json = ?,
                        verified_citation_ids = ?, verified_context_message_ids = ?,
                        clarification_fields = ?,
                        max_duration_seconds = ?,
                        remaining_seconds = CASE
                            WHEN deadline_at IS NULL THEN remaining_seconds
                            ELSE MAX(0, deadline_at - ?)
                        END,
                        deadline_at = NULL,
                        max_model_calls = ?, max_tool_calls = ?, model_call_count = ?,
                        tool_call_count = ?, model_retry_count = ?, tool_retry_count = ?,
                        input_tokens = ?, output_tokens = ?, usage_reported_model_calls = ?,
                        answer_message_id = ?, error_type = ?, end_reason = ?,
                        cancel_requested_at = ?, finished_at = ?, updated_at = ?
                    WHERE run_id = ? AND owner_id = ? AND status = 'running'
                      AND answer_message_id IS NULL
                      AND (deadline_at IS NULL OR deadline_at > ?)
                    """,
                    (
                        run.status,
                        run.stage,
                        json.dumps(run.evidence, ensure_ascii=False),
                        json.dumps(run.verified_citation_ids, ensure_ascii=False),
                        json.dumps(run.verified_context_message_ids, ensure_ascii=False),
                        json.dumps(run.clarification_fields, ensure_ascii=False),
                        run.max_duration_seconds,
                        run.updated_at,
                        run.max_model_calls,
                        run.max_tool_calls,
                        run.model_call_count,
                        run.tool_call_count,
                        run.model_retry_count,
                        run.tool_retry_count,
                        run.input_tokens,
                        run.output_tokens,
                        run.usage_reported_model_calls,
                        run.answer_message_id,
                        run.error_type,
                        run.end_reason,
                        run.cancel_requested_at,
                        run.finished_at,
                        run.updated_at,
                        run.run_id,
                        run.owner_id,
                        run.updated_at,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ConversationRunConflict(run.run_id)
                conn.execute(
                    "UPDATE tasks SET updated_at = ? WHERE task_id = ?",
                    (message.created_at, message.task_id),
                )
        except Exception:
            conn.rollback()
            raise

    def reserve_call(
        self,
        run_id: str,
        owner_id: str,
        *,
        kind: str,
        stage: str,
        now: float,
    ) -> ConversationRun | None:
        columns = {
            "model": ("model_call_count", "max_model_calls"),
            "tool": ("tool_call_count", "max_tool_calls"),
        }
        if kind not in columns:
            raise ValueError("未知调用类型")
        count_column, limit_column = columns[kind]
        conn = self._pool.get()
        cursor = conn.execute(
            f"""UPDATE conversation_runs
            SET {count_column} = {count_column} + 1, stage = ?, updated_at = ?
            WHERE run_id = ? AND owner_id = ? AND status = 'running'
              AND {count_column} < {limit_column}
              AND (deadline_at IS NULL OR deadline_at > ?)""",
            (stage, now, run_id, owner_id, now),
        )
        conn.commit()
        if cursor.rowcount != 1:
            return None
        return self.get(run_id, owner_id)

    def record_model_usage(
        self,
        run_id: str,
        owner_id: str,
        *,
        input_tokens: int | None,
        output_tokens: int | None,
        now: float,
    ) -> ConversationRun | None:
        if input_tokens is None or output_tokens is None:
            return self.get(run_id, owner_id)
        conn = self._pool.get()
        cursor = conn.execute(
            """UPDATE conversation_runs
            SET input_tokens = COALESCE(input_tokens, 0) + ?,
                output_tokens = COALESCE(output_tokens, 0) + ?,
                usage_reported_model_calls = usage_reported_model_calls + 1,
                updated_at = ?
            WHERE run_id = ? AND owner_id = ?
              AND status IN ('running', 'cancel_requested')""",
            (input_tokens, output_tokens, now, run_id, owner_id),
        )
        conn.commit()
        if cursor.rowcount != 1:
            return None
        return self.get(run_id, owner_id)

    def record_retry(
        self,
        run_id: str,
        owner_id: str,
        *,
        kind: str,
        now: float,
    ) -> ConversationRun | None:
        columns = {"model": "model_retry_count", "tool": "tool_retry_count"}
        if kind not in columns:
            raise ValueError("未知重试类型")
        column = columns[kind]
        conn = self._pool.get()
        cursor = conn.execute(
            f"""UPDATE conversation_runs SET {column} = {column} + 1, updated_at = ?
            WHERE run_id = ? AND owner_id = ? AND status = 'running'""",
            (now, run_id, owner_id),
        )
        conn.commit()
        if cursor.rowcount != 1:
            return None
        return self.get(run_id, owner_id)

    def request_cancel(
        self,
        run_id: str,
        owner_id: str,
        *,
        reason: str,
        now: float,
    ) -> ConversationRun | None:
        conn = self._pool.get()
        with conn:
            cursor = conn.execute(
                """UPDATE conversation_runs
                SET status = CASE WHEN status = 'interrupted' THEN 'cancelled'
                                  ELSE 'cancel_requested' END,
                    stage = CASE WHEN status = 'interrupted' THEN 'cancelled' ELSE stage END,
                    end_reason = COALESCE(end_reason, ?),
                    cancel_requested_at = COALESCE(cancel_requested_at, ?),
                    finished_at = CASE WHEN status = 'interrupted' THEN ? ELSE finished_at END,
                    remaining_seconds = CASE
                        WHEN deadline_at IS NULL THEN remaining_seconds
                        ELSE MAX(0, deadline_at - ?)
                    END,
                    deadline_at = CASE WHEN status = 'interrupted' THEN NULL ELSE deadline_at END,
                    updated_at = ?
                WHERE run_id = ? AND owner_id = ?
                  AND status IN ('running', 'interrupted')""",
                (reason, now, now, now, now, run_id, owner_id),
            )
        if cursor.rowcount == 0:
            return self.get(run_id, owner_id)
        return self.get(run_id, owner_id)

    def finish_active(
        self,
        run_id: str,
        owner_id: str,
        *,
        status: str,
        reason: str,
        error_type: str | None,
        now: float,
    ) -> ConversationRun | None:
        if status not in {"cancelled", "timed_out", "budget_exhausted", "failed"}:
            raise ValueError("非法无消息终态")
        conn = self._pool.get()
        with conn:
            conn.execute(
                """UPDATE conversation_runs
                SET status = ?, stage = ?, end_reason = ?, error_type = ?,
                    remaining_seconds = CASE
                        WHEN deadline_at IS NULL THEN remaining_seconds
                        ELSE MAX(0, deadline_at - ?)
                    END,
                    deadline_at = NULL, finished_at = ?, updated_at = ?
                WHERE run_id = ? AND owner_id = ?
                  AND status IN ('running', 'cancel_requested')
                  AND answer_message_id IS NULL""",
                (status, status, reason, error_type, now, now, now, run_id, owner_id),
            )
        return self.get(run_id, owner_id)

    def pause(
        self,
        run_id: str,
        owner_id: str,
        *,
        now: float,
    ) -> ConversationRun | None:
        conn = self._pool.get()
        with conn:
            conn.execute(
                """UPDATE conversation_runs
                SET status = 'interrupted', stage = 'agent',
                    remaining_seconds = CASE
                        WHEN deadline_at IS NULL THEN remaining_seconds
                        ELSE MAX(0, deadline_at - ?)
                    END,
                    deadline_at = NULL, updated_at = ?
                WHERE run_id = ? AND owner_id = ? AND status = 'running'
                  AND answer_message_id IS NULL
                  AND (deadline_at IS NULL OR deadline_at > ?)""",
                (now, now, run_id, owner_id, now),
            )
        return self.get(run_id, owner_id)

    def reconcile_stale(self, *, now: float) -> int:
        conn = self._pool.get()
        with conn:
            cancelled = conn.execute(
                """UPDATE conversation_runs
                SET status = 'cancelled', stage = 'cancelled',
                    end_reason = COALESCE(end_reason, 'user_cancelled'),
                    deadline_at = NULL, finished_at = ?, updated_at = ?
                WHERE status = 'cancel_requested' AND answer_message_id IS NULL""",
                (now, now),
            ).rowcount
            failed = conn.execute(
                """UPDATE conversation_runs
                SET status = 'failed', stage = 'failed', end_reason = 'runtime_restarted',
                    error_type = 'RuntimeRestarted',
                    remaining_seconds = CASE
                        WHEN deadline_at IS NULL THEN remaining_seconds
                        ELSE MAX(0, deadline_at - ?)
                    END,
                    deadline_at = NULL, finished_at = ?, updated_at = ?
                WHERE status = 'running' AND answer_message_id IS NULL""",
                (now, now, now),
            ).rowcount
        return cancelled + failed

    def get_latest_for_task(self, task_id: str, owner_id: str) -> ConversationRun | None:
        row = (
            self._pool.get()
            .execute(
                """
            SELECT * FROM conversation_runs
            WHERE task_id = ? AND owner_id = ?
            ORDER BY updated_at DESC LIMIT 1
            """,
                (task_id, owner_id),
            )
            .fetchone()
        )
        return None if row is None else _row_to_run(row)


def _values(run: ConversationRun) -> tuple[object, ...]:
    return (
        run.run_id,
        run.thread_id,
        run.owner_id,
        run.task_id,
        run.question,
        run.input_message_id,
        json.dumps(run.attachment_doc_ids, ensure_ascii=False),
        run.status,
        run.stage,
        json.dumps(run.evidence, ensure_ascii=False),
        json.dumps(run.verified_citation_ids, ensure_ascii=False),
        json.dumps(run.verified_context_message_ids, ensure_ascii=False),
        json.dumps(run.clarification_fields, ensure_ascii=False),
        run.max_duration_seconds,
        run.remaining_seconds,
        run.deadline_at,
        run.max_model_calls,
        run.max_tool_calls,
        run.model_call_count,
        run.tool_call_count,
        run.model_retry_count,
        run.tool_retry_count,
        run.input_tokens,
        run.output_tokens,
        run.usage_reported_model_calls,
        run.answer_message_id,
        run.error_type,
        run.end_reason,
        run.cancel_requested_at,
        run.finished_at,
        run.created_at,
        run.updated_at,
    )


def _row_to_run(row: Any) -> ConversationRun:
    return ConversationRun(
        run_id=row["run_id"],
        thread_id=row["thread_id"],
        owner_id=row["owner_id"],
        task_id=row["task_id"],
        question=row["question"],
        input_message_id=row["input_message_id"],
        attachment_doc_ids=json.loads(row["attachment_doc_ids"]),
        status=row["status"],
        stage=row["stage"],
        evidence=json.loads(row["evidence_json"]),
        verified_citation_ids=json.loads(row["verified_citation_ids"]),
        verified_context_message_ids=json.loads(row["verified_context_message_ids"]),
        clarification_fields=json.loads(row["clarification_fields"]),
        max_duration_seconds=row["max_duration_seconds"],
        remaining_seconds=row["remaining_seconds"],
        deadline_at=row["deadline_at"],
        max_model_calls=row["max_model_calls"],
        max_tool_calls=row["max_tool_calls"],
        model_call_count=row["model_call_count"],
        tool_call_count=row["tool_call_count"],
        model_retry_count=row["model_retry_count"],
        tool_retry_count=row["tool_retry_count"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        usage_reported_model_calls=row["usage_reported_model_calls"],
        answer_message_id=row["answer_message_id"],
        error_type=row["error_type"],
        end_reason=row["end_reason"],
        cancel_requested_at=row["cancel_requested_at"],
        finished_at=row["finished_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
