"""SQLAlchemy AgentRunRepoPort 实现。"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError

from domain.errors import AgentRunConflict
from domain.runs import AgentRun, RunCheckpoint, RunEvent
from infra.storage.sqlalchemy.database import SqlAlchemyDatabase
from infra.storage.sqlalchemy.mapping import (
    require_datetime,
    require_timestamp,
    to_datetime,
    to_timestamp,
)
from infra.storage.sqlalchemy.models import (
    AgentRunRow,
    CaseRow,
    RunCheckpointRow,
    RunEventRow,
)


class SqlAlchemyAgentRunRepo:
    def __init__(self, database: SqlAlchemyDatabase) -> None:
        self._database = database

    def create(
        self,
        run: AgentRun,
        checkpoint: RunCheckpoint,
        event: RunEvent,
    ) -> None:
        _validate_initial(run, checkpoint, event)
        try:
            with self._database.session() as session:
                workspace_id = session.scalar(
                    select(CaseRow.workspace_id).where(CaseRow.case_id == run.case_id)
                )
                if workspace_id != run.workspace_id:
                    raise ValueError("AgentRun 的 Workspace 与 Case 归属不一致")
                session.add(_run_row(run))
                session.add(_checkpoint_row(checkpoint))
                session.add(_event_row(event))
        except IntegrityError as exc:
            if _is_active_run_conflict(exc):
                raise AgentRunConflict(run.run_id) from exc
            raise

    def get(self, run_id: str) -> AgentRun | None:
        with self._database.read_session() as session:
            row = session.get(AgentRunRow, run_id)
            return None if row is None else _run(row)

    def get_checkpoint(self, checkpoint_id: str) -> RunCheckpoint | None:
        with self._database.read_session() as session:
            row = session.get(RunCheckpointRow, checkpoint_id)
            return None if row is None else _checkpoint(row)

    def get_latest_checkpoint(self, run_id: str) -> RunCheckpoint | None:
        statement = (
            select(RunCheckpointRow)
            .where(RunCheckpointRow.run_id == run_id)
            .order_by(RunCheckpointRow.version.desc())
            .limit(1)
        )
        with self._database.read_session() as session:
            row = session.scalar(statement)
            return None if row is None else _checkpoint(row)

    def list_for_case(
        self,
        case_id: str,
        *,
        limit: int = 50,
    ) -> list[AgentRun]:
        statement = (
            select(AgentRunRow)
            .where(AgentRunRow.case_id == case_id)
            .order_by(AgentRunRow.created_at.desc(), AgentRunRow.run_id.desc())
            .limit(limit)
        )
        with self._database.read_session() as session:
            return [_run(row) for row in session.scalars(statement)]

    def list_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 200,
    ) -> list[RunEvent]:
        statement = (
            select(RunEventRow)
            .where(
                RunEventRow.run_id == run_id,
                RunEventRow.sequence > after_sequence,
            )
            .order_by(RunEventRow.sequence)
            .limit(limit)
        )
        with self._database.read_session() as session:
            return [_event(row) for row in session.scalars(statement)]

    def next_checkpoint_version(self, run_id: str) -> int:
        statement = select(func.coalesce(func.max(RunCheckpointRow.version), 0) + 1).where(
            RunCheckpointRow.run_id == run_id
        )
        with self._database.read_session() as session:
            return int(session.execute(statement).scalar_one())

    def next_event_sequence(self, run_id: str) -> int:
        statement = select(func.coalesce(func.max(RunEventRow.sequence), 0) + 1).where(
            RunEventRow.run_id == run_id
        )
        with self._database.read_session() as session:
            return int(session.execute(statement).scalar_one())

    def save_progress(
        self,
        run: AgentRun,
        checkpoint: RunCheckpoint,
        events: list[RunEvent],
        *,
        expected_revision: int,
    ) -> None:
        _validate_progress(run, checkpoint, events, expected_revision)
        with self._database.session() as session:
            current = session.get(AgentRunRow, run.run_id)
            if current is None or current.revision != expected_revision:
                raise AgentRunConflict(run.run_id)
            if (
                current.workspace_id != run.workspace_id
                or current.case_id != run.case_id
                or current.workflow_type != run.workflow_type
                or current.thread_id != run.thread_id
                or current.created_by != run.created_by
                or require_timestamp(current.created_at) != run.created_at
            ):
                raise ValueError("AgentRun 的归属和创建字段不可修改")
            next_sequence = int(
                session.execute(
                    select(func.coalesce(func.max(RunEventRow.sequence), 0) + 1).where(
                        RunEventRow.run_id == run.run_id
                    )
                ).scalar_one()
            )
            if events:
                expected_sequences = list(range(next_sequence, next_sequence + len(events)))
                if [event.sequence for event in events] != expected_sequences:
                    raise ValueError("RunEvent sequence 必须从当前序号开始连续递增")
            result = cast(
                "CursorResult[Any]",
                session.execute(
                    update(AgentRunRow)
                    .where(
                        AgentRunRow.run_id == run.run_id,
                        AgentRunRow.revision == expected_revision,
                    )
                    .values(**_run_update_values(run))
                ),
            )
            if result.rowcount != 1:
                raise AgentRunConflict(run.run_id)
            session.add(_checkpoint_row(checkpoint))
            session.add_all(_event_row(event) for event in events)


def _validate_initial(
    run: AgentRun,
    checkpoint: RunCheckpoint,
    event: RunEvent,
) -> None:
    _validate_related(run, checkpoint, [event])
    if run.revision != 1 or checkpoint.version != 1:
        raise ValueError("新 Run 和首个 Checkpoint version 必须为 1")
    if event.sequence != 1 or event.event_type != "run_started":
        raise ValueError("首个 RunEvent 必须是 sequence=1 的 run_started")
    if run.checkpoint_id != checkpoint.checkpoint_id:
        raise ValueError("AgentRun.checkpoint_id 必须指向首个检查点")


def _validate_progress(
    run: AgentRun,
    checkpoint: RunCheckpoint,
    events: list[RunEvent],
    expected_revision: int,
) -> None:
    _validate_related(run, checkpoint, events)
    if run.revision != expected_revision + 1:
        raise ValueError("AgentRun revision 必须恰好递增 1")
    if checkpoint.version != run.revision:
        raise ValueError("RunCheckpoint version 必须与 AgentRun revision 一致")
    if run.checkpoint_id != checkpoint.checkpoint_id:
        raise ValueError("AgentRun.checkpoint_id 必须指向当前检查点")


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


def _run_row(run: AgentRun) -> AgentRunRow:
    return AgentRunRow(
        run_id=run.run_id,
        workspace_id=run.workspace_id,
        case_id=run.case_id,
        workflow_type=run.workflow_type,
        status=run.status,
        thread_id=run.thread_id,
        checkpoint_id=run.checkpoint_id,
        current_stage=run.current_stage,
        model_config_snapshot=run.model_config_snapshot,
        token_usage=run.token_usage,
        cost=run.cost,
        retry_count=run.retry_count,
        revision=run.revision,
        created_by=run.created_by,
        error_code=run.error_code,
        error_message=run.error_message,
        created_at=require_datetime(run.created_at),
        updated_at=require_datetime(run.updated_at),
        started_at=to_datetime(run.started_at),
        completed_at=to_datetime(run.completed_at),
    )


def _run_update_values(run: AgentRun) -> dict[str, object]:
    return {
        "status": run.status,
        "checkpoint_id": run.checkpoint_id,
        "current_stage": run.current_stage,
        "model_config_snapshot": run.model_config_snapshot,
        "token_usage": run.token_usage,
        "cost": run.cost,
        "retry_count": run.retry_count,
        "revision": run.revision,
        "error_code": run.error_code,
        "error_message": run.error_message,
        "updated_at": require_datetime(run.updated_at),
        "started_at": to_datetime(run.started_at),
        "completed_at": to_datetime(run.completed_at),
    }


def _checkpoint_row(checkpoint: RunCheckpoint) -> RunCheckpointRow:
    return RunCheckpointRow(
        checkpoint_id=checkpoint.checkpoint_id,
        run_id=checkpoint.run_id,
        thread_id=checkpoint.thread_id,
        version=checkpoint.version,
        stage=checkpoint.stage,
        state=checkpoint.state,
        created_at=require_datetime(checkpoint.created_at),
    )


def _event_row(event: RunEvent) -> RunEventRow:
    return RunEventRow(
        event_id=event.event_id,
        run_id=event.run_id,
        sequence=event.sequence,
        event_type=event.event_type,
        stage=event.stage,
        payload=event.payload,
        created_at=require_datetime(event.created_at),
    )


def _run(row: AgentRunRow) -> AgentRun:
    return AgentRun(
        run_id=row.run_id,
        workspace_id=row.workspace_id,
        case_id=row.case_id,
        workflow_type=row.workflow_type,
        status=row.status,
        thread_id=row.thread_id,
        checkpoint_id=row.checkpoint_id,
        current_stage=row.current_stage,
        model_config_snapshot=row.model_config_snapshot,
        token_usage=row.token_usage,
        cost=row.cost,
        retry_count=row.retry_count,
        revision=row.revision,
        created_by=row.created_by,
        error_code=row.error_code,
        error_message=row.error_message,
        created_at=require_timestamp(row.created_at),
        updated_at=require_timestamp(row.updated_at),
        started_at=to_timestamp(row.started_at),
        completed_at=to_timestamp(row.completed_at),
    )


def _checkpoint(row: RunCheckpointRow) -> RunCheckpoint:
    return RunCheckpoint(
        checkpoint_id=row.checkpoint_id,
        run_id=row.run_id,
        thread_id=row.thread_id,
        version=row.version,
        stage=row.stage,
        state=row.state,
        created_at=require_timestamp(row.created_at),
    )


def _event(row: RunEventRow) -> RunEvent:
    return RunEvent(
        event_id=row.event_id,
        run_id=row.run_id,
        sequence=row.sequence,
        event_type=row.event_type,
        stage=row.stage,
        payload=row.payload,
        created_at=require_timestamp(row.created_at),
    )


def _is_active_run_conflict(exc: IntegrityError) -> bool:
    message = str(exc.orig).lower()
    return (
        "uq_agent_runs_active_case_workflow" in message
        or "agent_runs.case_id, agent_runs.workflow_type" in message
    )
