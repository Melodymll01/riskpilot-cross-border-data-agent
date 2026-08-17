"""Case Assessment LangGraph 应用编排测试。"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from app.use_cases import (
    AssessmentManagementUseCase,
    CaseManagementUseCase,
    PolicyManagementUseCase,
    WorkspaceManagementUseCase,
)
from app.use_cases.assessment_runs import AssessmentRunUseCase
from config import Settings
from domain import (
    AgentRunAlreadyActive,
    AgentRunNotFound,
    CaseDocument,
    CaseFact,
    Document,
    DocumentVersion,
    EvidencePlanRequest,
    EvidencePlanResult,
    PolicyRule,
    ProcessingJob,
    WorkspaceAccessDenied,
)
from infra.agents import DeterministicEvidencePlanner
from infra.workflows import LangGraphWorkflowRuntime
from tests.fakes import (
    InMemoryAgentRunRepo,
    InMemoryAssessmentRepo,
    InMemoryCaseFactRepo,
    InMemoryCaseRepo,
    InMemoryDocumentRepo,
    InMemoryPolicyRuleRepo,
    InMemoryWorkspaceRepo,
)


class _Setup:
    def __init__(self, checkpoint_path: Path) -> None:
        self.workspace_repo = InMemoryWorkspaceRepo()
        self.case_repo = InMemoryCaseRepo()
        self.document_repo = InMemoryDocumentRepo()
        self.fact_repo = InMemoryCaseFactRepo()
        self.rule_repo = InMemoryPolicyRuleRepo()
        self.assessment_repo = InMemoryAssessmentRepo(self.case_repo)
        self.run_repo = InMemoryAgentRunRepo()
        self.workspace_uc = WorkspaceManagementUseCase(self.workspace_repo)
        self.case_uc = CaseManagementUseCase(
            case_repo=self.case_repo,
            workspace_repo=self.workspace_repo,
        )
        self.policy_uc = PolicyManagementUseCase(
            rule_repo=self.rule_repo,
            fact_repo=self.fact_repo,
            case_management=self.case_uc,
            workspace_management=self.workspace_uc,
        )
        self.assessment_uc = AssessmentManagementUseCase(
            assessment_repo=self.assessment_repo,
            fact_repo=self.fact_repo,
            document_repo=self.document_repo,
            case_management=self.case_uc,
            workspace_management=self.workspace_uc,
            policy_management=self.policy_uc,
        )
        self.runtime = LangGraphWorkflowRuntime(str(checkpoint_path))
        self.run_uc = self.build_run_use_case(self.runtime)

        workspace = self.workspace_uc.create_workspace(
            "github:alice",
            name="跨境合规组",
        )
        self.workspace_id = workspace.workspace_id
        for user_id, role in (
            ("github:editor", "editor"),
            ("github:reviewer", "reviewer"),
            ("github:viewer", "viewer"),
        ):
            self.workspace_uc.add_or_update_member(
                workspace.workspace_id,
                "github:alice",
                user_id=user_id,
                role=role,  # type: ignore[arg-type]
            )
        case = self.case_uc.create_case(
            "github:editor",
            workspace_id=workspace.workspace_id,
            title="海外客服项目",
            assessment_date=date(2026, 8, 7),
            reviewer_id="github:reviewer",
        )
        self.case_id = case.case_id
        self.case_uc.transition_case(case.case_id, "github:editor", "collecting")
        self.case_uc.transition_case(
            case.case_id,
            "github:editor",
            "ready_for_assessment",
        )
        self.publish_rule()

    def build_run_use_case(
        self,
        runtime: Any,
        *,
        settings: Settings | None = None,
    ) -> AssessmentRunUseCase:
        return AssessmentRunUseCase(
            run_repo=self.run_repo,
            workflow_runtime=runtime,
            document_repo=self.document_repo,
            fact_repo=self.fact_repo,
            case_management=self.case_uc,
            workspace_management=self.workspace_uc,
            policy_management=self.policy_uc,
            assessment_management=self.assessment_uc,
            settings=settings,
        )

    def publish_rule(self) -> None:
        rule = PolicyRule(
            workspace_id=self.workspace_id,
            rule_id="SYNTHETIC-001",
            ruleset_version="synthetic-v1",
            jurisdiction="CN",
            effective_from=date(2026, 1, 1),
            status="draft",
            required_fact_fields=["important_data_involved"],
            condition={
                "field": "important_data_involved",
                "operator": "eq",
                "value": True,
            },
            result={
                "risk_level": "high",
                "candidate_path": "security_assessment",
                "required_actions": ["提交安全评估材料"],
            },
            source_clause_ids=["synthetic-clause"],
        )
        self.policy_uc.create_rule(self.workspace_id, "github:alice", rule)
        self.policy_uc.publish_rule(
            self.workspace_id,
            "github:alice",
            rule_id=rule.rule_id,
            ruleset_version=rule.ruleset_version,
        )

    def seed_document(self, *, status: str = "ready") -> str:
        document_id = "document_001"
        version_id = "version_001"
        document = Document(
            document_id=document_id,
            workspace_id=self.workspace_id,
            logical_name="case.txt",
            document_type="case_material",
            status=status,  # type: ignore[arg-type]
            created_by="github:editor",
            current_version_id=version_id,
            created_at=100.0,
            updated_at=100.0,
        )
        version = DocumentVersion(
            version_id=version_id,
            document_id=document_id,
            version_number=1,
            object_key="objects/case.txt",
            sha256=hashlib.sha256(b"case material").hexdigest(),
            mime_type="text/plain",
            size_bytes=13,
            page_count=1,
            created_at=100.0,
        )
        binding = CaseDocument(
            case_id=self.case_id,
            document_id=document_id,
            added_by="github:editor",
            added_at=100.0,
        )
        job = ProcessingJob(
            job_id="job_001",
            document_version_id=version_id,
            status="completed" if status == "ready" else "queued",
            current_stage="ready" if status == "ready" else "validate",
            progress=1.0 if status == "ready" else 0.0,
            created_at=100.0,
            updated_at=101.0 if status == "ready" else 100.0,
            started_at=100.0 if status == "ready" else None,
            completed_at=101.0 if status == "ready" else None,
        )
        self.document_repo.create_upload(document, version, binding, job)
        return document_id

    def mark_document_ready(self, document_id: str) -> None:
        document = self.document_repo.get(document_id)
        assert document is not None
        self.document_repo.update_document(
            Document(
                **{
                    **document.model_dump(),
                    "status": "ready",
                    "updated_at": max(102.0, document.updated_at),
                }
            )
        )

    def confirm_fact(self) -> None:
        self.fact_repo.create(
            CaseFact(
                fact_id="fact_important_data",
                case_id=self.case_id,
                field_name="important_data_involved",
                value=True,
                status="confirmed",
                source_type="user",
                confidence=1.0,
                criticality="critical",
                created_by="github:editor",
                confirmed_by="github:reviewer",
                confirmed_at=101.0,
                created_at=100.0,
                updated_at=101.0,
            ),
            [],
        )


@pytest.fixture
def setup(tmp_path: Path) -> _Setup:
    return _Setup(tmp_path / "langgraph.sqlite3")


class TestAssessmentRunLifecycle:
    def test_ready_case_generates_assessment_and_waits_for_review(
        self,
        setup: _Setup,
    ) -> None:
        setup.seed_document()
        setup.confirm_fact()

        run = setup.run_uc.start(
            setup.case_id,
            "github:editor",
            ruleset_version="synthetic-v1",
            model_config_snapshot={"provider": "deterministic"},
        )

        assert run.status == "waiting_for_review"
        assert run.current_stage == "human_review"
        active = setup.assessment_uc.get_active(setup.case_id, "github:editor")
        assert active is not None
        assert active.assessment.generated_by_run_id == run.run_id
        assert active.assessment.status == "review_required"
        case = setup.case_repo.get(setup.case_id)
        assert case is not None
        assert case.status == "review_required"
        events = setup.run_uc.list_events(run.run_id, "github:editor")
        assert events[0].event_type == "run_started"
        assert events[-1].event_type == "human_review_required"
        assert [event.sequence for event in events] == list(range(1, len(events) + 1))
        assert all("thought" not in event.payload for event in events)

    def test_document_then_fact_interrupts_resume_to_review(
        self,
        setup: _Setup,
    ) -> None:
        document_id = setup.seed_document(status="queued")
        run = setup.run_uc.start(
            setup.case_id,
            "github:editor",
            ruleset_version="synthetic-v1",
        )
        assert run.status == "waiting_for_user"
        assert run.current_stage == "inspect_documents"

        repeated = setup.run_uc.continue_run(run.run_id, "github:editor")
        assert repeated.status == "waiting_for_user"
        assert repeated.current_stage == "inspect_documents"
        assert repeated.revision == run.revision

        setup.mark_document_ready(document_id)
        missing = setup.run_uc.continue_run(run.run_id, "github:editor")
        assert missing.status == "waiting_for_user"
        assert missing.current_stage == "human_fact_confirmation"

        setup.confirm_fact()
        review = setup.run_uc.continue_run(run.run_id, "github:editor")
        assert review.status == "waiting_for_review"
        assert review.current_stage == "human_review"

    def test_reviewer_approval_completes_assessment_case_and_run(
        self,
        setup: _Setup,
    ) -> None:
        setup.seed_document()
        setup.confirm_fact()
        run = setup.run_uc.start(
            setup.case_id,
            "github:editor",
            ruleset_version="synthetic-v1",
        )

        completed = setup.run_uc.review_run(
            run.run_id,
            "github:reviewer",
            decision="approved",
            comment="证据与规则核验通过",
        )

        assert completed.status == "completed"
        assert completed.completed_at is not None
        active = setup.assessment_uc.get_active(setup.case_id, "github:reviewer")
        assert active is not None
        assert active.assessment.status == "approved"
        case = setup.case_repo.get(setup.case_id)
        assert case is not None
        assert case.status == "completed"
        assert setup.run_uc.list_events(run.run_id, "github:reviewer")[-1].event_type == (
            "run_completed"
        )

    def test_real_token_usage_and_explicit_price_are_persisted(
        self,
        setup: _Setup,
    ) -> None:
        class _UsagePlanner:
            def build_plan(self, request: EvidencePlanRequest) -> EvidencePlanResult:
                planned = DeterministicEvidencePlanner().build_plan(request)
                return planned.model_copy(
                    update={
                        "input_tokens": 100,
                        "output_tokens": 20,
                        "token_usage": 120,
                    }
                )

        setup.seed_document()
        setup.confirm_fact()
        settings = Settings(  # type: ignore[call-arg]
            _env_file=None,
            llm_provider="local",
            embed_provider="local",
            local_chat_model="test-cost-model",
            llm_input_cost_per_1m_tokens=2.0,
            llm_output_cost_per_1m_tokens=8.0,
            llm_cost_currency="CNY",
        )
        runtime = LangGraphWorkflowRuntime(
            ":memory:",
            planner=_UsagePlanner(),
            model_name=settings.effective_chat_model,
            input_cost_per_1m_tokens=settings.llm_input_cost_per_1m_tokens,
            output_cost_per_1m_tokens=settings.llm_output_cost_per_1m_tokens,
        )
        run_uc = setup.build_run_use_case(runtime, settings=settings)

        run = run_uc.start(
            setup.case_id,
            "github:editor",
            ruleset_version="synthetic-v1",
            model_config_snapshot={"model": "client-must-not-override"},
        )

        assert run.status == "waiting_for_review"
        assert run.token_usage == 120
        assert run.cost == pytest.approx(0.00036)
        assert run.model_config_snapshot == {
            "model": "test-cost-model",
            "provider": "local",
            "input_cost_per_1m_tokens": 2.0,
            "output_cost_per_1m_tokens": 8.0,
            "cost_currency": "CNY",
        }

    def test_unconfigured_price_keeps_cost_zero(self, setup: _Setup) -> None:
        class _UsagePlanner:
            def build_plan(self, request: EvidencePlanRequest) -> EvidencePlanResult:
                planned = DeterministicEvidencePlanner().build_plan(request)
                return planned.model_copy(
                    update={
                        "input_tokens": 60,
                        "output_tokens": 20,
                        "token_usage": 80,
                    }
                )

        setup.seed_document()
        setup.confirm_fact()
        settings = Settings(  # type: ignore[call-arg]
            _env_file=None,
            llm_provider="local",
            embed_provider="local",
        )
        run_uc = setup.build_run_use_case(
            LangGraphWorkflowRuntime(":memory:", planner=_UsagePlanner()),
            settings=settings,
        )

        run = run_uc.start(
            setup.case_id,
            "github:editor",
            ruleset_version="synthetic-v1",
        )

        assert run.token_usage == 80
        assert run.cost == 0.0
        assert run.model_config_snapshot["input_cost_per_1m_tokens"] == 0.0
        assert run.model_config_snapshot["output_cost_per_1m_tokens"] == 0.0

    def test_reviewer_rejection_completes_run_and_returns_case_for_reassessment(
        self,
        setup: _Setup,
    ) -> None:
        setup.seed_document()
        setup.confirm_fact()
        run = setup.run_uc.start(
            setup.case_id,
            "github:editor",
            ruleset_version="synthetic-v1",
        )

        completed = setup.run_uc.review_run(
            run.run_id,
            "github:reviewer",
            decision="rejected",
            comment="需补充材料",
        )

        assert completed.status == "completed"
        active = setup.assessment_uc.get_active(setup.case_id, "github:reviewer")
        assert active is not None
        assert active.assessment.status == "rejected"
        case = setup.case_repo.get(setup.case_id)
        assert case is not None
        assert case.status == "ready_for_assessment"


class TestAssessmentRunSafety:
    def test_cancel_waiting_run_is_idempotent_and_blocks_continue(
        self,
        setup: _Setup,
    ) -> None:
        setup.seed_document(status="queued")
        run = setup.run_uc.start(
            setup.case_id,
            "github:editor",
            ruleset_version="synthetic-v1",
        )

        cancelled = setup.run_uc.cancel_run(run.run_id, "github:editor")
        repeated = setup.run_uc.cancel_run(run.run_id, "github:editor")

        assert cancelled.status == "cancelled"
        assert repeated == cancelled
        assert setup.run_uc.list_events(run.run_id, "github:editor")[-1].event_type == (
            "run_cancelled"
        )
        with pytest.raises(ValueError, match="可以继续"):
            setup.run_uc.continue_run(run.run_id, "github:editor")

    def test_same_case_cannot_start_two_active_runs(self, setup: _Setup) -> None:
        setup.seed_document(status="queued")
        first = setup.run_uc.start(
            setup.case_id,
            "github:editor",
            ruleset_version="synthetic-v1",
        )
        with pytest.raises(AgentRunAlreadyActive) as exc_info:
            setup.run_uc.start(
                setup.case_id,
                "github:editor",
                ruleset_version="synthetic-v1",
            )
        assert exc_info.value.run_id == first.run_id

    def test_viewer_cannot_start_or_continue_run(self, setup: _Setup) -> None:
        setup.seed_document(status="queued")
        with pytest.raises(WorkspaceAccessDenied):
            setup.run_uc.start(
                setup.case_id,
                "github:viewer",
                ruleset_version="synthetic-v1",
            )
        run = setup.run_uc.start(
            setup.case_id,
            "github:editor",
            ruleset_version="synthetic-v1",
        )
        with pytest.raises(WorkspaceAccessDenied):
            setup.run_uc.continue_run(run.run_id, "github:viewer")

    def test_outsider_cannot_discover_run(self, setup: _Setup) -> None:
        setup.seed_document(status="queued")
        run = setup.run_uc.start(
            setup.case_id,
            "github:editor",
            ruleset_version="synthetic-v1",
        )
        with pytest.raises(AgentRunNotFound):
            setup.run_uc.get(run.run_id, "github:outsider")

    def test_model_snapshot_rejects_secret_before_run_is_created(
        self,
        setup: _Setup,
    ) -> None:
        setup.seed_document()
        setup.confirm_fact()
        with pytest.raises(ValueError, match="敏感"):
            setup.run_uc.start(
                setup.case_id,
                "github:editor",
                ruleset_version="synthetic-v1",
                model_config_snapshot={"api_key": "secret"},
            )
        assert setup.run_repo.list_for_case(setup.case_id) == []


class _FlakyGenerationRuntime:
    def __init__(self, inner: LangGraphWorkflowRuntime) -> None:
        self.inner = inner
        self.failed_once = False

    def inspect_case_assessment(self, **kwargs: Any) -> Any:
        return self.inner.inspect_case_assessment(**kwargs)

    def start_case_assessment(self, **kwargs: Any) -> Any:
        return self.inner.start_case_assessment(**kwargs)

    def resume_case_assessment(self, **kwargs: Any) -> Any:
        if "assessment_id" in kwargs.get("resume_value", {}) and not self.failed_once:
            self.failed_once = True
            raise RuntimeError("simulated crash after assessment persistence")
        return self.inner.resume_case_assessment(**kwargs)


class TestAssessmentRunRecovery:
    def test_retry_reuses_assessment_generated_before_crash(
        self,
        setup: _Setup,
    ) -> None:
        setup.seed_document()
        setup.confirm_fact()
        flaky = _FlakyGenerationRuntime(setup.runtime)
        run_uc = setup.build_run_use_case(flaky)

        with pytest.raises(RuntimeError, match="simulated crash"):
            run_uc.start(
                setup.case_id,
                "github:editor",
                ruleset_version="synthetic-v1",
            )

        failed = setup.run_repo.list_for_case(setup.case_id)[0]
        assert failed.status == "failed"
        assert failed.error_message is not None
        assert "simulated crash" not in failed.error_message
        first_assessment = setup.assessment_uc.get_active(
            setup.case_id,
            "github:editor",
        )
        assert first_assessment is not None
        assert first_assessment.assessment.version == 1

        recovered = run_uc.retry_run(failed.run_id, "github:editor")

        assert recovered.status == "waiting_for_review"
        active = setup.assessment_uc.get_active(setup.case_id, "github:editor")
        assert active is not None
        assert active.assessment.assessment_id == first_assessment.assessment.assessment_id
        assert active.assessment.version == 1
        events = setup.run_uc.list_events(failed.run_id, "github:editor")
        assert any(event.event_type == "run_failed" for event in events)
        assert any(event.event_type == "run_retrying" for event in events)
