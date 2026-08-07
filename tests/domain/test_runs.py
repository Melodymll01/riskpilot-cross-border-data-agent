"""V2 AgentRun、轻量检查点与事件模型测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from domain import (
    AgentRun,
    InvalidAgentRunTransition,
    RunCheckpoint,
    RunEvent,
)


def _run(**overrides: object) -> AgentRun:
    values: dict[str, object] = {
        "run_id": "run_001",
        "workspace_id": "ws_001",
        "case_id": "case_001",
        "workflow_type": "case_assessment",
        "thread_id": "thread_001",
        "checkpoint_id": "checkpoint_001",
        "current_stage": "queued",
        "model_config_snapshot": {"provider": "mock", "model": "deterministic"},
        "created_by": "github:alice",
        "created_at": 100.0,
        "updated_at": 100.0,
    }
    values.update(overrides)
    return AgentRun(**values)  # type: ignore[arg-type]


class TestAgentRun:
    def test_pause_resume_and_complete_path(self) -> None:
        queued = _run()
        running = queued.start(
            checkpoint_id="checkpoint_002",
            stage="validate_documents",
            at=101.0,
        )
        waiting = running.pause_for_user(
            checkpoint_id="checkpoint_003",
            stage="request_fact_confirmation",
            at=102.0,
        )
        resumed = waiting.resume(
            checkpoint_id="checkpoint_004",
            stage="evaluate_policy_rules",
            at=103.0,
        )
        completed = resumed.complete(
            checkpoint_id="checkpoint_005",
            at=104.0,
        )

        assert running.status == "running"
        assert running.started_at == 101.0
        assert waiting.status == "waiting_for_user"
        assert resumed.status == "running"
        assert completed.status == "completed"
        assert completed.completed_at == 104.0
        assert completed.revision == 5

    def test_failed_run_can_enter_retrying_and_resume(self) -> None:
        running = _run().start(
            checkpoint_id="checkpoint_002",
            stage="validate_documents",
            at=101.0,
        )
        failed = running.fail(
            checkpoint_id="checkpoint_003",
            stage="validate_documents",
            error_code="DOCUMENT_NOT_READY",
            at=102.0,
        )
        retrying = failed.mark_retrying(
            checkpoint_id="checkpoint_004",
            stage="validate_documents",
            at=103.0,
        )
        resumed = retrying.resume(
            checkpoint_id="checkpoint_005",
            stage="validate_documents",
            at=104.0,
        )

        assert failed.error_code == "DOCUMENT_NOT_READY"
        assert retrying.retry_count == 1
        assert retrying.completed_at is None
        assert resumed.status == "running"

    def test_invalid_direct_completion_rejected(self) -> None:
        with pytest.raises(InvalidAgentRunTransition):
            _run().complete(checkpoint_id="checkpoint_002", at=101.0)

    def test_usage_cannot_go_backwards(self) -> None:
        running = _run().start(
            checkpoint_id="checkpoint_002",
            stage="extract_fact_candidates",
            at=101.0,
        )
        advanced = running.advance(
            checkpoint_id="checkpoint_003",
            stage="merge_existing_facts",
            token_usage=100,
            cost=0.2,
            at=102.0,
        )
        with pytest.raises(ValueError, match="token_usage"):
            advanced.advance(
                checkpoint_id="checkpoint_004",
                stage="detect_missing_facts",
                token_usage=99,
                at=103.0,
            )

    def test_queued_run_can_be_cancelled_before_start(self) -> None:
        cancelled = _run().cancel(
            checkpoint_id="checkpoint_002",
            stage="cancelled",
            at=101.0,
        )
        assert cancelled.status == "cancelled"
        assert cancelled.started_at is None
        assert cancelled.completed_at == 101.0


class TestRunStateSafety:
    def test_checkpoint_rejects_nested_reasoning(self) -> None:
        with pytest.raises(ValidationError, match="原始推理"):
            RunCheckpoint(
                checkpoint_id="checkpoint_001",
                run_id="run_001",
                thread_id="thread_001",
                version=1,
                stage="queued",
                state={"nested": {"reasoning": "private chain of thought"}},
                created_at=100.0,
            )

    def test_event_rejects_credentials(self) -> None:
        with pytest.raises(ValidationError, match="敏感"):
            RunEvent(
                event_id="event_001",
                run_id="run_001",
                sequence=1,
                event_type="run_started",
                stage="queued",
                payload={"api_key": "secret"},
                created_at=100.0,
            )

    def test_checkpoint_rejects_oversized_state(self) -> None:
        with pytest.raises(ValidationError, match="字节限制"):
            RunCheckpoint(
                checkpoint_id="checkpoint_001",
                run_id="run_001",
                thread_id="thread_001",
                version=1,
                stage="queued",
                state={"value": "x" * (64 * 1024)},
                created_at=100.0,
            )

    def test_safe_event_round_trip(self) -> None:
        event = RunEvent(
            event_id="event_001",
            run_id="run_001",
            sequence=1,
            event_type="stage_completed",
            stage="validate_documents",
            payload={"document_ids": ["document_001"], "count": 1},
            created_at=100.0,
        )
        assert RunEvent.model_validate_json(event.model_dump_json()) == event
