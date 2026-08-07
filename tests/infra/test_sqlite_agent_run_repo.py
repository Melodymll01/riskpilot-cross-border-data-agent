"""SqliteAgentRunRepo 检查点、事件和乐观锁测试。"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from domain import (
    AgentRun,
    AgentRunConflict,
    AgentRunRepoPort,
    Case,
    RunCheckpoint,
    RunEvent,
    Workspace,
    WorkspaceMembership,
)
from infra.storage import (
    SqliteAgentRunRepo,
    SqliteCaseRepo,
    SqliteWorkspaceRepo,
)
from infra.storage._db import SqliteConnectionPool


@pytest.fixture
def pool(tmp_path: Path) -> SqliteConnectionPool:
    return SqliteConnectionPool(str(tmp_path / "runs.db"))


@pytest.fixture
def repo(pool: SqliteConnectionPool) -> SqliteAgentRunRepo:
    _seed_case(pool)
    return SqliteAgentRunRepo(pool)


def _seed_case(pool: SqliteConnectionPool) -> None:
    SqliteWorkspaceRepo(pool).create(
        Workspace(
            workspace_id="ws_001",
            name="跨境合规组",
            created_by="github:alice",
            created_at=100.0,
            updated_at=100.0,
        ),
        WorkspaceMembership(
            workspace_id="ws_001",
            user_id="github:alice",
            role="admin",
            joined_at=100.0,
        ),
    )
    SqliteCaseRepo(pool).create(
        Case(
            case_id="case_001",
            workspace_id="ws_001",
            title="案件",
            assessment_date=date(2026, 8, 7),
            owner_id="github:alice",
            created_at=100.0,
            updated_at=100.0,
        )
    )


def _initial() -> tuple[AgentRun, RunCheckpoint, RunEvent]:
    run = AgentRun(
        run_id="run_001",
        workspace_id="ws_001",
        case_id="case_001",
        workflow_type="case_assessment",
        thread_id="thread_001",
        checkpoint_id="checkpoint_001",
        current_stage="queued",
        model_config_snapshot={"provider": "mock", "model": "deterministic"},
        created_by="github:alice",
        created_at=100.0,
        updated_at=100.0,
    )
    checkpoint = RunCheckpoint(
        checkpoint_id="checkpoint_001",
        run_id=run.run_id,
        thread_id=run.thread_id,
        version=1,
        stage="queued",
        state={"case_id": run.case_id, "next_node": "load_case"},
        created_at=100.0,
    )
    event = RunEvent(
        event_id="event_001",
        run_id=run.run_id,
        sequence=1,
        event_type="run_started",
        stage="queued",
        payload={"workflow_type": run.workflow_type},
        created_at=100.0,
    )
    return run, checkpoint, event


def _progress(run: AgentRun) -> tuple[AgentRun, RunCheckpoint, list[RunEvent]]:
    updated = run.start(
        checkpoint_id="checkpoint_002",
        stage="load_case",
        at=101.0,
    )
    checkpoint = RunCheckpoint(
        checkpoint_id="checkpoint_002",
        run_id=run.run_id,
        thread_id=run.thread_id,
        version=2,
        stage="load_case",
        state={"case_id": run.case_id, "next_node": "authorize"},
        created_at=101.0,
    )
    events = [
        RunEvent(
            event_id="event_002",
            run_id=run.run_id,
            sequence=2,
            event_type="stage_started",
            stage="load_case",
            created_at=101.0,
        ),
        RunEvent(
            event_id="event_003",
            run_id=run.run_id,
            sequence=3,
            event_type="stage_completed",
            stage="load_case",
            payload={"case_id": run.case_id},
            created_at=101.0,
        ),
    ]
    return updated, checkpoint, events


class TestSqliteAgentRunRepo:
    def test_satisfies_port(self, repo: SqliteAgentRunRepo) -> None:
        assert isinstance(repo, AgentRunRepoPort)

    def test_create_and_round_trip(self, repo: SqliteAgentRunRepo) -> None:
        run, checkpoint, event = _initial()
        repo.create(run, checkpoint, event)

        assert repo.get(run.run_id) == run
        assert repo.get_checkpoint(checkpoint.checkpoint_id) == checkpoint
        assert repo.get_latest_checkpoint(run.run_id) == checkpoint
        assert repo.list_events(run.run_id) == [event]
        assert repo.list_for_case(run.case_id) == [run]
        assert repo.next_checkpoint_version(run.run_id) == 2
        assert repo.next_event_sequence(run.run_id) == 2

    def test_workspace_case_mismatch_rejected(
        self,
        repo: SqliteAgentRunRepo,
    ) -> None:
        run, checkpoint, event = _initial()
        mismatched = run.model_copy(update={"workspace_id": "ws_other"})

        with pytest.raises(ValueError, match="归属不一致"):
            repo.create(mismatched, checkpoint, event)

        assert repo.get(run.run_id) is None

    def test_save_progress_updates_all_artifacts_atomically(
        self,
        repo: SqliteAgentRunRepo,
    ) -> None:
        run, initial_checkpoint, initial_event = _initial()
        repo.create(run, initial_checkpoint, initial_event)
        updated, checkpoint, events = _progress(run)

        repo.save_progress(
            updated,
            checkpoint,
            events,
            expected_revision=run.revision,
        )

        assert repo.get(run.run_id) == updated
        assert repo.get_latest_checkpoint(run.run_id) == checkpoint
        assert repo.list_events(run.run_id) == [initial_event, *events]
        assert repo.list_events(run.run_id, after_sequence=1) == events

    def test_stale_revision_rejected_without_partial_write(
        self,
        repo: SqliteAgentRunRepo,
    ) -> None:
        run, initial_checkpoint, initial_event = _initial()
        repo.create(run, initial_checkpoint, initial_event)
        updated, checkpoint, events = _progress(run)
        repo.save_progress(
            updated,
            checkpoint,
            events,
            expected_revision=run.revision,
        )

        stale_run = run.start(
            checkpoint_id="checkpoint_stale",
            stage="authorize",
            at=102.0,
        )
        stale_checkpoint = RunCheckpoint(
            checkpoint_id="checkpoint_stale",
            run_id=run.run_id,
            thread_id=run.thread_id,
            version=2,
            stage="authorize",
            state={"next_node": "validate_documents"},
            created_at=102.0,
        )
        stale_event = RunEvent(
            event_id="event_stale",
            run_id=run.run_id,
            sequence=4,
            event_type="stage_started",
            stage="authorize",
            created_at=102.0,
        )
        with pytest.raises(AgentRunConflict):
            repo.save_progress(
                stale_run,
                stale_checkpoint,
                [stale_event],
                expected_revision=run.revision,
            )

        assert repo.get(run.run_id) == updated
        assert repo.get_checkpoint("checkpoint_stale") is None
        assert repo.list_events(run.run_id) == [initial_event, *events]

    def test_non_contiguous_event_sequence_rolls_back_run_update(
        self,
        repo: SqliteAgentRunRepo,
    ) -> None:
        run, initial_checkpoint, initial_event = _initial()
        repo.create(run, initial_checkpoint, initial_event)
        updated, checkpoint, events = _progress(run)
        invalid_event = events[0].model_copy(update={"sequence": 3})

        with pytest.raises(ValueError, match="连续"):
            repo.save_progress(
                updated,
                checkpoint,
                [invalid_event],
                expected_revision=run.revision,
            )

        assert repo.get(run.run_id) == run
        assert repo.get_latest_checkpoint(run.run_id) == initial_checkpoint
        assert repo.list_events(run.run_id) == [initial_event]

    def test_invalid_checkpoint_insert_rolls_back_run_update(
        self,
        repo: SqliteAgentRunRepo,
    ) -> None:
        run, initial_checkpoint, initial_event = _initial()
        repo.create(run, initial_checkpoint, initial_event)
        updated, checkpoint, events = _progress(run)
        duplicate_checkpoint = checkpoint.model_copy(
            update={"checkpoint_id": initial_checkpoint.checkpoint_id}
        )
        updated = updated.model_copy(update={"checkpoint_id": initial_checkpoint.checkpoint_id})

        with pytest.raises(sqlite3.IntegrityError):
            repo.save_progress(
                updated,
                duplicate_checkpoint,
                events,
                expected_revision=run.revision,
            )

        assert repo.get(run.run_id) == run
        assert repo.list_events(run.run_id) == [initial_event]
