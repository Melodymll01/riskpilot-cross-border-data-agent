"""SQLite AgentRunRepoPort 实现。"""

from __future__ import annotations

import json
from typing import Any, cast

from domain.errors import AgentRunConflict
from domain.runs import (
    AgentRun,
    AgentRunStatus,
    RunCheckpoint,
    RunEvent,
    RunEventType,
    WorkflowType,
)
from infra.storage._db import SqliteConnectionPool


class SqliteAgentRunRepo:
    def __init__(self, pool: SqliteConnectionPool) -> None:
        self._pool = pool

    def create(
        self,
        run: AgentRun,
        checkpoint: RunCheckpoint,
        event: RunEvent,
    ) -> None:
        _validate_related(run, checkpoint, [event])
        if run.revision != 1:
            raise ValueError("新 AgentRun revision 必须为 1")
        if checkpoint.version != 1:
            raise ValueError("首个 RunCheckpoint version 必须为 1")
        if event.sequence != 1 or event.event_type != "run_started":
            raise ValueError("首个 RunEvent 必须是 sequence=1 的 run_started")
        if run.checkpoint_id != checkpoint.checkpoint_id:
            raise ValueError("AgentRun.checkpoint_id 必须指向首个检查点")
        conn = self._pool.get()
        with conn:
            case_row = conn.execute(
                "SELECT workspace_id FROM compliance_cases WHERE case_id = ?",
                (run.case_id,),
            ).fetchone()
            if case_row is None or case_row["workspace_id"] != run.workspace_id:
                raise ValueError("AgentRun 的 Workspace 与 Case 归属不一致")
            conn.execute(
                """
                INSERT INTO agent_runs
                    (run_id, workspace_id, case_id, workflow_type, status, thread_id,
                     checkpoint_id, current_stage, model_config_json, token_usage,
                     cost, retry_count, revision, created_by, error_code,
                     error_message, created_at, updated_at, started_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _run_values(run),
            )
            _insert_checkpoint(conn, checkpoint)
            _insert_events(conn, [event])

    def get(self, run_id: str) -> AgentRun | None:
        row = (
            self._pool.get()
            .execute("SELECT * FROM agent_runs WHERE run_id = ?", (run_id,))
            .fetchone()
        )
        return None if row is None else _row_to_run(row)

    def get_checkpoint(self, checkpoint_id: str) -> RunCheckpoint | None:
        row = (
            self._pool.get()
            .execute(
                "SELECT * FROM run_checkpoints WHERE checkpoint_id = ?",
                (checkpoint_id,),
            )
            .fetchone()
        )
        return None if row is None else _row_to_checkpoint(row)

    def get_latest_checkpoint(self, run_id: str) -> RunCheckpoint | None:
        row = (
            self._pool.get()
            .execute(
                """
                SELECT * FROM run_checkpoints
                WHERE run_id = ?
                ORDER BY version DESC
                LIMIT 1
                """,
                (run_id,),
            )
            .fetchone()
        )
        return None if row is None else _row_to_checkpoint(row)

    def list_for_case(
        self,
        case_id: str,
        *,
        limit: int = 50,
    ) -> list[AgentRun]:
        rows = (
            self._pool.get()
            .execute(
                """
                SELECT * FROM agent_runs
                WHERE case_id = ?
                ORDER BY created_at DESC, run_id DESC
                LIMIT ?
                """,
                (case_id, limit),
            )
            .fetchall()
        )
        return [_row_to_run(row) for row in rows]

    def list_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 200,
    ) -> list[RunEvent]:
        rows = (
            self._pool.get()
            .execute(
                """
                SELECT * FROM run_events
                WHERE run_id = ? AND sequence > ?
                ORDER BY sequence
                LIMIT ?
                """,
                (run_id, after_sequence, limit),
            )
            .fetchall()
        )
        return [_row_to_event(row) for row in rows]

    def next_checkpoint_version(self, run_id: str) -> int:
        row = (
            self._pool.get()
            .execute(
                """
                SELECT COALESCE(MAX(version), 0) + 1 AS next_version
                FROM run_checkpoints
                WHERE run_id = ?
                """,
                (run_id,),
            )
            .fetchone()
        )
        return int(row["next_version"])

    def next_event_sequence(self, run_id: str) -> int:
        row = (
            self._pool.get()
            .execute(
                """
                SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence
                FROM run_events
                WHERE run_id = ?
                """,
                (run_id,),
            )
            .fetchone()
        )
        return int(row["next_sequence"])

    def save_progress(
        self,
        run: AgentRun,
        checkpoint: RunCheckpoint,
        events: list[RunEvent],
        *,
        expected_revision: int,
    ) -> None:
        _validate_related(run, checkpoint, events)
        if run.revision != expected_revision + 1:
            raise ValueError("AgentRun revision 必须恰好递增 1")
        if checkpoint.version != run.revision:
            raise ValueError("RunCheckpoint version 必须与 AgentRun revision 一致")
        if run.checkpoint_id != checkpoint.checkpoint_id:
            raise ValueError("AgentRun.checkpoint_id 必须指向当前检查点")
        conn = self._pool.get()
        with conn:
            current = conn.execute(
                "SELECT * FROM agent_runs WHERE run_id = ?",
                (run.run_id,),
            ).fetchone()
            if current is None or current["revision"] != expected_revision:
                raise AgentRunConflict(run.run_id)
            if (
                current["workspace_id"] != run.workspace_id
                or current["case_id"] != run.case_id
                or current["workflow_type"] != run.workflow_type
                or current["thread_id"] != run.thread_id
                or current["created_by"] != run.created_by
                or current["created_at"] != run.created_at
            ):
                raise ValueError("AgentRun 的归属和创建字段不可修改")
            next_sequence = int(
                conn.execute(
                    """
                    SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence
                    FROM run_events
                    WHERE run_id = ?
                    """,
                    (run.run_id,),
                ).fetchone()["next_sequence"]
            )
            if events:
                sequences = [event.sequence for event in events]
                expected_sequences = list(range(next_sequence, next_sequence + len(events)))
                if sequences != expected_sequences:
                    raise ValueError("RunEvent sequence 必须从当前序号开始连续递增")
            cursor = conn.execute(
                """
                UPDATE agent_runs SET
                    status = ?, checkpoint_id = ?, current_stage = ?,
                    model_config_json = ?, token_usage = ?, cost = ?, retry_count = ?,
                    revision = ?, error_code = ?, error_message = ?,
                    updated_at = ?, started_at = ?, completed_at = ?
                WHERE run_id = ? AND revision = ?
                """,
                (
                    run.status,
                    run.checkpoint_id,
                    run.current_stage,
                    json.dumps(run.model_config_snapshot, ensure_ascii=False, allow_nan=False),
                    run.token_usage,
                    run.cost,
                    run.retry_count,
                    run.revision,
                    run.error_code,
                    run.error_message,
                    run.updated_at,
                    run.started_at,
                    run.completed_at,
                    run.run_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise AgentRunConflict(run.run_id)
            _insert_checkpoint(conn, checkpoint)
            _insert_events(conn, events)


def _validate_related(
    run: AgentRun,
    checkpoint: RunCheckpoint,
    events: list[RunEvent],
) -> None:
    if checkpoint.run_id != run.run_id:
        raise ValueError("RunCheckpoint 必须属于当前 AgentRun")
    if checkpoint.thread_id != run.thread_id:
        raise ValueError("RunCheckpoint.thread_id 必须与 AgentRun 一致")
    if checkpoint.stage != run.current_stage:
        raise ValueError("RunCheckpoint.stage 必须与 AgentRun.current_stage 一致")
    if any(event.run_id != run.run_id for event in events):
        raise ValueError("RunEvent 必须属于当前 AgentRun")
    sequences = [event.sequence for event in events]
    if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
        raise ValueError("RunEvent sequence 必须严格递增且不能重复")


def _insert_checkpoint(conn: Any, checkpoint: RunCheckpoint) -> None:
    conn.execute(
        """
        INSERT INTO run_checkpoints
            (checkpoint_id, run_id, thread_id, version, stage, state_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            checkpoint.checkpoint_id,
            checkpoint.run_id,
            checkpoint.thread_id,
            checkpoint.version,
            checkpoint.stage,
            json.dumps(checkpoint.state, ensure_ascii=False, allow_nan=False),
            checkpoint.created_at,
        ),
    )


def _insert_events(conn: Any, events: list[RunEvent]) -> None:
    conn.executemany(
        """
        INSERT INTO run_events
            (event_id, run_id, sequence, event_type, stage, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                event.event_id,
                event.run_id,
                event.sequence,
                event.event_type,
                event.stage,
                json.dumps(event.payload, ensure_ascii=False, allow_nan=False),
                event.created_at,
            )
            for event in events
        ],
    )


def _run_values(run: AgentRun) -> tuple[object, ...]:
    return (
        run.run_id,
        run.workspace_id,
        run.case_id,
        run.workflow_type,
        run.status,
        run.thread_id,
        run.checkpoint_id,
        run.current_stage,
        json.dumps(run.model_config_snapshot, ensure_ascii=False, allow_nan=False),
        run.token_usage,
        run.cost,
        run.retry_count,
        run.revision,
        run.created_by,
        run.error_code,
        run.error_message,
        run.created_at,
        run.updated_at,
        run.started_at,
        run.completed_at,
    )


def _row_to_run(row: Any) -> AgentRun:
    return AgentRun(
        run_id=row["run_id"],
        workspace_id=row["workspace_id"],
        case_id=row["case_id"],
        workflow_type=_validate_workflow_type(row["workflow_type"]),
        status=_validate_run_status(row["status"]),
        thread_id=row["thread_id"],
        checkpoint_id=row["checkpoint_id"],
        current_stage=row["current_stage"],
        model_config_snapshot=json.loads(row["model_config_json"]),
        token_usage=row["token_usage"],
        cost=row["cost"],
        retry_count=row["retry_count"],
        revision=row["revision"],
        created_by=row["created_by"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
    )


def _row_to_checkpoint(row: Any) -> RunCheckpoint:
    return RunCheckpoint(
        checkpoint_id=row["checkpoint_id"],
        run_id=row["run_id"],
        thread_id=row["thread_id"],
        version=row["version"],
        stage=row["stage"],
        state=json.loads(row["state_json"]),
        created_at=row["created_at"],
    )


def _row_to_event(row: Any) -> RunEvent:
    return RunEvent(
        event_id=row["event_id"],
        run_id=row["run_id"],
        sequence=row["sequence"],
        event_type=_validate_event_type(row["event_type"]),
        stage=row["stage"],
        payload=json.loads(row["payload_json"]),
        created_at=row["created_at"],
    )


def _validate_workflow_type(value: str) -> WorkflowType:
    if value not in {"case_assessment", "deep_research"}:
        raise ValueError(f"invalid workflow type in DB: {value!r}")
    return cast("WorkflowType", value)


def _validate_run_status(value: str) -> AgentRunStatus:
    valid = {
        "queued",
        "running",
        "waiting_for_user",
        "waiting_for_review",
        "retrying",
        "completed",
        "failed",
        "cancelled",
    }
    if value not in valid:
        raise ValueError(f"invalid agent run status in DB: {value!r}")
    return cast("AgentRunStatus", value)


def _validate_event_type(value: str) -> RunEventType:
    valid = {
        "run_started",
        "stage_started",
        "stage_progress",
        "stage_completed",
        "tool_started",
        "tool_completed",
        "evidence_found",
        "facts_proposed",
        "fact_confirmation_required",
        "conflict_detected",
        "human_input_required",
        "human_review_required",
        "artifact_ready",
        "run_paused",
        "run_resumed",
        "run_retrying",
        "run_failed",
        "run_completed",
        "run_cancelled",
    }
    if value not in valid:
        raise ValueError(f"invalid run event type in DB: {value!r}")
    return cast("RunEventType", value)
