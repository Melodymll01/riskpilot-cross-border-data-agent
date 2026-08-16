"""LangGraph 案件评估运行时的中断、恢复与隔离测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from domain import CaseDocumentReadiness, WorkflowRuntimePort
from infra.workflows import LangGraphWorkflowRuntime
from tests.fakes import FakeTrace


@pytest.fixture
def checkpoint_path(tmp_path: Path) -> str:
    return str(tmp_path / "langgraph.sqlite3")


def _runtime(checkpoint_path: str) -> LangGraphWorkflowRuntime:
    return LangGraphWorkflowRuntime(checkpoint_path)


class TestLangGraphInterruptResume:
    def test_satisfies_workflow_runtime_port(self, checkpoint_path: str) -> None:
        assert isinstance(_runtime(checkpoint_path), WorkflowRuntimePort)

    def test_start_and_resume_record_structured_trace_metadata(
        self,
        checkpoint_path: str,
    ) -> None:
        trace = FakeTrace()
        runtime = LangGraphWorkflowRuntime(checkpoint_path, trace=trace)

        started = runtime.start_case_assessment(
            thread_id="thread_trace",
            case_id="case_sensitive",
            workspace_id="ws_sensitive",
            actor_id="github:alice",
            ruleset_version="synthetic-v1",
            document_readiness=CaseDocumentReadiness(
                pending_document_ids=["document_sensitive"]
            ),
            missing_fact_fields=["important_data_involved"],
        )
        resumed = runtime.resume_case_assessment(
            thread_id="thread_trace",
            resume_value={"action": "retry"},
            state_update={
                "ready_document_ids": ["document_sensitive"],
                "pending_document_ids": [],
            },
        )

        assert started.stage == "validate_documents"
        assert resumed.stage == "detect_missing_facts"
        assert [span["name"] for span in trace.spans] == [
            "riskpilot.case_assessment.start",
            "riskpilot.case_assessment.resume",
        ]
        start_metadata = trace.spans[0]["metadata"]
        assert start_metadata["pending_document_count"] == 1
        assert start_metadata["missing_fact_count"] == 1
        assert start_metadata["status"] == "interrupted"
        assert "document_sensitive" not in str(start_metadata)
        resume_metadata = trace.spans[1]["metadata"]
        assert resume_metadata["resumed"] is True
        assert resume_metadata["interrupt_kind"] == "fact_confirmation"

    def test_full_chain_survives_runtime_recreation(self, checkpoint_path: str) -> None:
        first = _runtime(checkpoint_path).start_case_assessment(
            thread_id="thread_001",
            case_id="case_001",
            workspace_id="ws_001",
            actor_id="github:alice",
            ruleset_version="synthetic-v1",
            document_readiness=CaseDocumentReadiness(pending_document_ids=["document_001"]),
            missing_fact_fields=["important_data_involved"],
        )
        assert first.status == "interrupted"
        assert first.stage == "validate_documents"
        assert first.interrupt is not None
        assert first.interrupt.kind == "documents_required"
        assert first.completed_stages == ["load_case", "authorize"]

        second = _runtime(checkpoint_path).resume_case_assessment(
            thread_id="thread_001",
            resume_value={"action": "retry"},
            state_update={
                "ready_document_ids": ["document_001"],
                "pending_document_ids": [],
            },
        )
        assert second.status == "interrupted"
        assert second.stage == "detect_missing_facts"
        assert second.interrupt is not None
        assert second.interrupt.kind == "fact_confirmation"

        third = _runtime(checkpoint_path).resume_case_assessment(
            thread_id="thread_001",
            resume_value={"action": "retry"},
            state_update={"missing_fact_fields": []},
        )
        assert third.status == "interrupted"
        assert third.stage == "draft_assessment"
        assert third.interrupt is not None
        assert third.interrupt.kind == "assessment_generation"
        assert third.completed_stages == [
            "detect_missing_facts",
            "select_policy_snapshot",
            "evaluate_policy_rules",
        ]

        fourth = _runtime(checkpoint_path).resume_case_assessment(
            thread_id="thread_001",
            resume_value={"assessment_id": "assessment_001"},
        )
        assert fourth.status == "interrupted"
        assert fourth.stage == "human_review"
        assert fourth.interrupt is not None
        assert fourth.interrupt.kind == "assessment_review"
        assert fourth.state["assessment_id"] == "assessment_001"

        completed = _runtime(checkpoint_path).resume_case_assessment(
            thread_id="thread_001",
            resume_value={"decision": "approved"},
        )
        assert completed.status == "completed"
        assert completed.stage == "complete"
        assert completed.state["review_decision"] == "approved"
        assert completed.completed_stages == ["human_review", "complete"]

    def test_ready_case_reaches_generation_interrupt_directly(
        self,
        checkpoint_path: str,
    ) -> None:
        result = _runtime(checkpoint_path).start_case_assessment(
            thread_id="thread_ready",
            case_id="case_001",
            workspace_id="ws_001",
            actor_id="github:alice",
            ruleset_version="synthetic-v1",
            document_readiness=CaseDocumentReadiness(ready_document_ids=["document_001"]),
            missing_fact_fields=[],
        )
        assert result.stage == "draft_assessment"
        assert result.interrupt is not None
        assert result.interrupt.kind == "assessment_generation"
        assert result.completed_stages == [
            "load_case",
            "authorize",
            "validate_documents",
            "detect_missing_facts",
            "select_policy_snapshot",
            "evaluate_policy_rules",
        ]

    def test_rejected_review_also_completes_graph(self, checkpoint_path: str) -> None:
        runtime = _runtime(checkpoint_path)
        generated = runtime.start_case_assessment(
            thread_id="thread_rejected",
            case_id="case_001",
            workspace_id="ws_001",
            actor_id="github:alice",
            ruleset_version="synthetic-v1",
            document_readiness=CaseDocumentReadiness(ready_document_ids=["document_001"]),
            missing_fact_fields=[],
        )
        assert generated.interrupt is not None
        reviewed = runtime.resume_case_assessment(
            thread_id="thread_rejected",
            resume_value={"assessment_id": "assessment_001"},
        )
        assert reviewed.stage == "human_review"
        completed = runtime.resume_case_assessment(
            thread_id="thread_rejected",
            resume_value={"decision": "rejected"},
        )
        assert completed.status == "completed"
        assert completed.state["review_decision"] == "rejected"


class TestLangGraphSafety:
    def test_duplicate_thread_rejected(self, checkpoint_path: str) -> None:
        runtime = _runtime(checkpoint_path)
        kwargs = {
            "thread_id": "thread_duplicate",
            "case_id": "case_001",
            "workspace_id": "ws_001",
            "actor_id": "github:alice",
            "ruleset_version": "synthetic-v1",
            "document_readiness": CaseDocumentReadiness(ready_document_ids=["document_001"]),
            "missing_fact_fields": [],
        }
        runtime.start_case_assessment(**kwargs)
        with pytest.raises(ValueError, match="已存在"):
            runtime.start_case_assessment(**kwargs)

    def test_resume_rejects_unknown_or_sensitive_fields(
        self,
        checkpoint_path: str,
    ) -> None:
        runtime = _runtime(checkpoint_path)
        runtime.start_case_assessment(
            thread_id="thread_invalid",
            case_id="case_001",
            workspace_id="ws_001",
            actor_id="github:alice",
            ruleset_version="synthetic-v1",
            document_readiness=CaseDocumentReadiness(pending_document_ids=["document_001"]),
            missing_fact_fields=[],
        )
        with pytest.raises(ValueError, match="非法字段"):
            runtime.resume_case_assessment(
                thread_id="thread_invalid",
                resume_value={"action": "retry", "raw_prompt": "do not store"},
            )
        with pytest.raises(ValueError, match="非法字段"):
            runtime.resume_case_assessment(
                thread_id="thread_invalid",
                resume_value={"action": "retry"},
                state_update={"document_text": "sensitive material"},
            )

    def test_resume_rejects_wrong_interrupt_action(self, checkpoint_path: str) -> None:
        runtime = _runtime(checkpoint_path)
        runtime.start_case_assessment(
            thread_id="thread_action",
            case_id="case_001",
            workspace_id="ws_001",
            actor_id="github:alice",
            ruleset_version="synthetic-v1",
            document_readiness=CaseDocumentReadiness(),
            missing_fact_fields=[],
        )
        with pytest.raises(ValueError, match="action=retry"):
            runtime.resume_case_assessment(
                thread_id="thread_action",
                resume_value={"action": "skip"},
            )

    def test_resume_cannot_bypass_unresolved_documents(
        self,
        checkpoint_path: str,
    ) -> None:
        runtime = _runtime(checkpoint_path)
        runtime.start_case_assessment(
            thread_id="thread_pending",
            case_id="case_001",
            workspace_id="ws_001",
            actor_id="github:alice",
            ruleset_version="synthetic-v1",
            document_readiness=CaseDocumentReadiness(pending_document_ids=["document_001"]),
            missing_fact_fields=[],
        )
        with pytest.raises(ValueError, match="ready"):
            runtime.resume_case_assessment(
                thread_id="thread_pending",
                resume_value={"action": "retry"},
                state_update={
                    "ready_document_ids": [],
                    "pending_document_ids": ["document_001"],
                },
            )

    def test_resume_cannot_bypass_unresolved_facts(
        self,
        checkpoint_path: str,
    ) -> None:
        runtime = _runtime(checkpoint_path)
        runtime.start_case_assessment(
            thread_id="thread_missing",
            case_id="case_001",
            workspace_id="ws_001",
            actor_id="github:alice",
            ruleset_version="synthetic-v1",
            document_readiness=CaseDocumentReadiness(ready_document_ids=["document_001"]),
            missing_fact_fields=["important_data_involved"],
        )
        with pytest.raises(ValueError, match="必须为空"):
            runtime.resume_case_assessment(
                thread_id="thread_missing",
                resume_value={"action": "retry"},
                state_update={
                    "missing_fact_fields": ["important_data_involved"],
                },
            )

    def test_thread_state_is_isolated(self, checkpoint_path: str) -> None:
        runtime = _runtime(checkpoint_path)
        first = runtime.start_case_assessment(
            thread_id="thread_a",
            case_id="case_a",
            workspace_id="ws_a",
            actor_id="github:a",
            ruleset_version="rules-a",
            document_readiness=CaseDocumentReadiness(),
            missing_fact_fields=[],
        )
        second = runtime.start_case_assessment(
            thread_id="thread_b",
            case_id="case_b",
            workspace_id="ws_b",
            actor_id="github:b",
            ruleset_version="rules-b",
            document_readiness=CaseDocumentReadiness(),
            missing_fact_fields=[],
        )
        assert first.state["case_id"] == "case_a"
        assert second.state["case_id"] == "case_b"
        assert first.checkpoint_id != second.checkpoint_id

    def test_completed_thread_cannot_resume(self, checkpoint_path: str) -> None:
        runtime = _runtime(checkpoint_path)
        runtime.start_case_assessment(
            thread_id="thread_done",
            case_id="case_001",
            workspace_id="ws_001",
            actor_id="github:alice",
            ruleset_version="synthetic-v1",
            document_readiness=CaseDocumentReadiness(ready_document_ids=["document_001"]),
            missing_fact_fields=[],
        )
        runtime.resume_case_assessment(
            thread_id="thread_done",
            resume_value={"assessment_id": "assessment_001"},
        )
        runtime.resume_case_assessment(
            thread_id="thread_done",
            resume_value={"decision": "approved"},
        )
        with pytest.raises(ValueError, match="没有可恢复中断"):
            runtime.resume_case_assessment(
                thread_id="thread_done",
                resume_value={"decision": "approved"},
            )
