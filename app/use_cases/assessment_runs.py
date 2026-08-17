"""V2 Case Assessment LangGraph 运行编排用例。"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

from app.use_cases.fact_management import FactDetail
from domain.agent_workflow import EvidencePlan
from domain.errors import (
    AgentRunAlreadyActive,
    AgentRunConflict,
    AgentRunNotFound,
    CaseNotFound,
    WorkspaceAccessDenied,
)
from domain.runs import (
    AgentRun,
    CaseDocumentReadiness,
    RunCheckpoint,
    RunEvent,
    RunEventType,
    WorkflowExecutionResult,
)
from domain.workspaces import WorkspaceRole

if TYPE_CHECKING:
    from app.use_cases.assessment_management import AssessmentManagementUseCase
    from app.use_cases.case_management import CaseManagementUseCase
    from app.use_cases.policy_management import PolicyManagementUseCase
    from app.use_cases.workspace_management import WorkspaceManagementUseCase
    from config import Settings
    from domain.assessments import AssessmentBundle, AssessmentStatus
    from domain.ports import (
        AgentRunRepoPort,
        CaseFactRepoPort,
        DocumentRepoPort,
        WorkflowRuntimePort,
    )

_WRITE_ROLES: set[WorkspaceRole] = {"editor", "reviewer", "admin"}
_ACTIVE_STATUSES = {
    "queued",
    "running",
    "waiting_for_user",
    "waiting_for_review",
    "retrying",
}
_SAFE_RUN_FAILURE_MESSAGE = "Agent Run 执行失败；详细原因仅记录于受控服务日志。"


@dataclass(frozen=True)
class RunTimelineItem:
    sequence: int
    event_type: str
    stage: str | None
    status: str
    summary: str
    duration_ms: int
    created_at: float


@dataclass(frozen=True)
class RunToolCallDetail:
    sequence: int
    tool_name: str
    stage: str | None
    arguments: dict[str, Any]
    result_summary: str
    output: dict[str, Any]
    duration_ms: int
    retry_count: int
    token_usage: int
    created_at: float


@dataclass(frozen=True)
class RunInterruptDetail:
    kind: str
    stage: str | None
    reason: str
    missing_fact_fields: list[str]
    conflict_field_names: list[str]
    candidate_fact_ids: list[str]
    created_at: float


@dataclass(frozen=True)
class RunActionCapabilities:
    can_continue: bool
    can_retry: bool
    can_cancel: bool
    can_review: bool


@dataclass(frozen=True)
class AssessmentRunDetail:
    run: AgentRun
    duration_ms: int
    cost_currency: str
    timeline: list[RunTimelineItem]
    evidence_plan: EvidencePlan | None
    tool_calls: list[RunToolCallDetail]
    interrupt: RunInterruptDetail | None
    facts: list[FactDetail]
    rule_evaluation: dict[str, Any] | None
    citation_verification: dict[str, Any] | None
    assessment: AssessmentBundle | None
    actions: RunActionCapabilities


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class AssessmentRunUseCase:
    """把领域 Repository、确定性 Assessment 与 LangGraph 恢复状态编排起来。"""

    def __init__(
        self,
        *,
        run_repo: AgentRunRepoPort,
        workflow_runtime: WorkflowRuntimePort,
        document_repo: DocumentRepoPort,
        fact_repo: CaseFactRepoPort,
        case_management: CaseManagementUseCase,
        workspace_management: WorkspaceManagementUseCase,
        policy_management: PolicyManagementUseCase,
        assessment_management: AssessmentManagementUseCase,
        settings: Settings | None = None,
    ) -> None:
        self._runs = run_repo
        self._runtime = workflow_runtime
        self._documents = document_repo
        self._facts = fact_repo
        self._case_management = case_management
        self._workspace_management = workspace_management
        self._policies = policy_management
        self._assessments = assessment_management
        self._settings = settings

    def start(
        self,
        case_id: str,
        actor_id: str,
        *,
        ruleset_version: str,
        model_config_snapshot: dict[str, Any] | None = None,
    ) -> AgentRun:
        case = self._case_management.get_case(case_id, actor_id)
        membership = self._workspace_management.require_role(
            case.workspace_id,
            actor_id,
            _WRITE_ROLES,
            action="启动 Case Assessment Run",
        )
        if case.assessment_date is None:
            raise ValueError("案件必须设置 assessment_date 才能启动 Assessment Run")
        if case.status not in {"ready_for_assessment", "review_required"}:
            raise ValueError(
                "案件必须处于 ready_for_assessment 或 review_required 才能启动 Assessment Run"
            )
        self._ensure_no_active_run(case.case_id)
        self._require_published_rules(
            workspace_id=case.workspace_id,
            actor_id=actor_id,
            jurisdiction=case.jurisdiction,
            assessment_date=case.assessment_date,
            ruleset_version=ruleset_version,
        )
        readiness = self._document_readiness(case.case_id)
        required_fields, missing_fields = self._fact_requirements(
            case_id=case.case_id,
            workspace_id=case.workspace_id,
            actor_id=actor_id,
            jurisdiction=case.jurisdiction,
            assessment_date=case.assessment_date,
            ruleset_version=ruleset_version,
        )
        now = time.time()
        run_id = _new_id("run")
        thread_id = f"case-assessment:{run_id}"
        initial_checkpoint_id = _new_id("run_checkpoint")
        runtime_snapshot = {
            **(model_config_snapshot or {}),
            **self._runtime_model_config_snapshot(),
        }
        run = AgentRun(
            run_id=run_id,
            workspace_id=case.workspace_id,
            case_id=case.case_id,
            workflow_type="case_assessment",
            thread_id=thread_id,
            checkpoint_id=initial_checkpoint_id,
            current_stage="queued",
            model_config_snapshot=runtime_snapshot,
            created_by=actor_id,
            created_at=now,
            updated_at=now,
        )
        initial_checkpoint = RunCheckpoint(
            checkpoint_id=initial_checkpoint_id,
            run_id=run.run_id,
            thread_id=run.thread_id,
            version=1,
            stage="queued",
            state={
                "case_id": case.case_id,
                "workspace_id": case.workspace_id,
                "ruleset_version": ruleset_version,
                "next_action": "start",
            },
            created_at=now,
        )
        initial_event = RunEvent(
            event_id=_new_id("run_event"),
            run_id=run.run_id,
            sequence=1,
            event_type="run_started",
            stage="queued",
            payload={"workflow_type": "case_assessment"},
            created_at=now,
        )
        self._runs.create(run, initial_checkpoint, initial_event)

        running = self._persist_running(run, ruleset_version=ruleset_version)
        try:
            result = self._runtime.start_case_assessment(
                thread_id=run.thread_id,
                case_id=case.case_id,
                workspace_id=case.workspace_id,
                actor_id=actor_id,
                actor_role=membership.role,
                ruleset_version=ruleset_version,
                document_readiness=readiness,
                missing_fact_fields=missing_fields,
                conflict_field_names=self._conflict_field_names(case.case_id),
                required_fact_fields=required_fields,
                run_id=run.run_id,
            )
            return self._handle_execution_result(running, result, actor_id=actor_id)
        except Exception as exc:
            self._persist_failure(running, exc)
            raise

    def _runtime_model_config_snapshot(self) -> dict[str, Any]:
        if self._settings is None:
            return {}
        return {
            "model": self._settings.effective_chat_model,
            "provider": self._settings.llm_provider,
            "input_cost_per_1m_tokens": self._settings.llm_input_cost_per_1m_tokens,
            "output_cost_per_1m_tokens": self._settings.llm_output_cost_per_1m_tokens,
            "cost_currency": self._settings.llm_cost_currency,
        }

    def continue_run(self, run_id: str, actor_id: str) -> AgentRun:
        run = self._get_authorized_run(run_id, actor_id, write=True)
        if run.status not in {"waiting_for_user", "waiting_for_review", "retrying", "running"}:
            raise ValueError("只有等待用户、等待冲突审核、重试中或待对账的 Run 可以继续")
        try:
            return self._continue_run(run, actor_id)
        except (AgentRunConflict, WorkspaceAccessDenied):
            raise
        except Exception as exc:
            self._persist_failure(run, exc)
            raise

    def _continue_run(self, run: AgentRun, actor_id: str) -> AgentRun:
        inspected = self._runtime.inspect_case_assessment(thread_id=run.thread_id)
        if inspected is None:
            if run.status != "retrying":
                raise ValueError("LangGraph thread 不存在，不能继续")
            case = self._case_management.get_case(run.case_id, actor_id)
            membership = self._workspace_management.require_role(
                case.workspace_id,
                actor_id,
                _WRITE_ROLES,
                action="恢复 Case Assessment Run",
            )
            assessment_date = _require_assessment_date(case.assessment_date)
            ruleset_version = self._run_ruleset_version(run)
            required_fields, missing_fields = self._fact_requirements(
                case_id=case.case_id,
                workspace_id=case.workspace_id,
                actor_id=actor_id,
                jurisdiction=case.jurisdiction,
                assessment_date=assessment_date,
                ruleset_version=ruleset_version,
            )
            result = self._runtime.start_case_assessment(
                thread_id=run.thread_id,
                case_id=case.case_id,
                workspace_id=case.workspace_id,
                actor_id=actor_id,
                actor_role=membership.role,
                ruleset_version=ruleset_version,
                document_readiness=self._document_readiness(case.case_id),
                missing_fact_fields=missing_fields,
                conflict_field_names=self._conflict_field_names(case.case_id),
                required_fact_fields=required_fields,
                run_id=run.run_id,
            )
            return self._handle_execution_result(run, result, actor_id=actor_id)
        if run.status == "running":
            return self._handle_execution_result(run, inspected, actor_id=actor_id)

        case = self._case_management.get_case(run.case_id, actor_id)
        membership = self._workspace_management.require_role(
            case.workspace_id,
            actor_id,
            _WRITE_ROLES,
            action="继续 Case Assessment Run",
        )
        assessment_date = _require_assessment_date(case.assessment_date)
        if inspected.interrupt is None:
            return self._handle_execution_result(run, inspected, actor_id=actor_id)
        kind = inspected.interrupt.kind
        if kind == "documents_required":
            readiness = self._document_readiness(run.case_id)
            if readiness.blocked:
                return run
            result = self._runtime.resume_case_assessment(
                thread_id=run.thread_id,
                resume_value={"action": "retry"},
                state_update=readiness.model_dump(),
                actor_id=actor_id,
                actor_role=membership.role,
            )
        elif kind == "fact_confirmation":
            missing_fields = self._missing_fact_fields(
                case_id=case.case_id,
                workspace_id=case.workspace_id,
                actor_id=actor_id,
                jurisdiction=case.jurisdiction,
                assessment_date=assessment_date,
                ruleset_version=_ruleset_version(inspected),
            )
            if missing_fields:
                return run
            result = self._runtime.resume_case_assessment(
                thread_id=run.thread_id,
                resume_value={"action": "retry"},
                state_update={
                    "missing_fact_fields": missing_fields,
                    "candidate_fact_ids": self._candidate_fact_ids(case.case_id),
                },
                actor_id=actor_id,
                actor_role=membership.role,
            )
        elif kind == "fact_conflict_review":
            self._workspace_management.require_role(
                case.workspace_id,
                actor_id,
                {"reviewer", "admin"},
                action="处理 Agent 发现的冲突事实",
            )
            conflict_fields = self._conflict_field_names(case.case_id)
            if conflict_fields:
                return run
            missing_fields = self._missing_fact_fields(
                case_id=case.case_id,
                workspace_id=case.workspace_id,
                actor_id=actor_id,
                jurisdiction=case.jurisdiction,
                assessment_date=assessment_date,
                ruleset_version=_ruleset_version(inspected),
            )
            result = self._runtime.resume_case_assessment(
                thread_id=run.thread_id,
                resume_value={"action": "retry"},
                state_update={
                    "conflict_field_names": conflict_fields,
                    "missing_fact_fields": missing_fields,
                },
                actor_id=actor_id,
                actor_role=membership.role,
            )
        elif kind == "assessment_generation":
            result = self._generate_assessment_and_resume(run, actor_id, inspected)
        else:
            return run
        return self._handle_execution_result(run, result, actor_id=actor_id)

    def retry_run(self, run_id: str, actor_id: str) -> AgentRun:
        run = self._get_authorized_run(run_id, actor_id, write=True)
        if run.status != "failed":
            raise ValueError("只有 failed Run 可以重试")
        now = max(time.time(), run.updated_at)
        checkpoint = RunCheckpoint(
            checkpoint_id=_new_id("run_checkpoint"),
            run_id=run.run_id,
            thread_id=run.thread_id,
            version=run.revision + 1,
            stage=run.current_stage,
            state={
                **self._latest_checkpoint_state(run),
                "next_action": "retry",
            },
            created_at=now,
        )
        retrying = run.mark_retrying(
            checkpoint_id=checkpoint.checkpoint_id,
            stage=run.current_stage,
            at=now,
        )
        event = RunEvent(
            event_id=_new_id("run_event"),
            run_id=run.run_id,
            sequence=self._runs.next_event_sequence(run.run_id),
            event_type="run_retrying",
            stage=run.current_stage,
            payload={"retry_count": retrying.retry_count},
            created_at=now,
        )
        self._runs.save_progress(
            retrying,
            checkpoint,
            [event],
            expected_revision=run.revision,
        )
        return self.continue_run(run_id, actor_id)

    def cancel_run(self, run_id: str, actor_id: str) -> AgentRun:
        run = self._get_authorized_run(run_id, actor_id, write=True)
        if run.status == "cancelled":
            return run
        if run.status in {"completed", "failed"}:
            raise ValueError("completed 或 failed Run 不能取消")
        now = max(time.time(), run.updated_at)
        checkpoint = RunCheckpoint(
            checkpoint_id=_new_id("run_checkpoint"),
            run_id=run.run_id,
            thread_id=run.thread_id,
            version=run.revision + 1,
            stage=run.current_stage,
            state={
                **self._latest_checkpoint_state(run),
                "cancelled": True,
            },
            created_at=now,
        )
        cancelled = run.cancel(
            checkpoint_id=checkpoint.checkpoint_id,
            stage=run.current_stage,
            at=now,
        )
        event = RunEvent(
            event_id=_new_id("run_event"),
            run_id=run.run_id,
            sequence=self._runs.next_event_sequence(run.run_id),
            event_type="run_cancelled",
            stage=run.current_stage,
            created_at=now,
        )
        self._runs.save_progress(
            cancelled,
            checkpoint,
            [event],
            expected_revision=run.revision,
        )
        return cancelled

    def review_run(
        self,
        run_id: str,
        actor_id: str,
        *,
        decision: AssessmentStatus,
        comment: str = "",
    ) -> AgentRun:
        run = self._get_authorized_run(run_id, actor_id, write=False)
        if run.status != "waiting_for_review":
            raise ValueError("只有 waiting_for_review Run 可以审批")
        inspected = self._runtime.inspect_case_assessment(thread_id=run.thread_id)
        if inspected is None:
            raise ValueError("LangGraph thread 不存在")
        if inspected.status == "completed":
            return self._handle_execution_result(run, inspected, actor_id=actor_id)
        if inspected.interrupt is None or inspected.interrupt.kind != "assessment_review":
            raise ValueError("LangGraph 当前不在 Assessment Review 中断")
        assessment_id = inspected.interrupt.payload.get("assessment_id")
        if not isinstance(assessment_id, str) or not assessment_id:
            raise ValueError("Assessment Review 中断缺少 assessment_id")
        reviewed = self._assessments.review(
            assessment_id,
            actor_id,
            decision=decision,
            comment=comment,
        )
        try:
            membership = self._workspace_management.require_role(
                run.workspace_id,
                actor_id,
                {"reviewer", "admin"},
                action="审批 Case Assessment",
            )
            result = self._runtime.resume_case_assessment(
                thread_id=run.thread_id,
                resume_value={"decision": reviewed.assessment.status},
                actor_id=actor_id,
                actor_role=membership.role,
            )
            return self._handle_execution_result(run, result, actor_id=actor_id)
        except AgentRunConflict:
            raise
        except Exception as exc:
            self._persist_failure(run, exc)
            raise

    def get(self, run_id: str, actor_id: str) -> AgentRun:
        return self._get_authorized_run(run_id, actor_id, write=False)

    def list_for_case(self, case_id: str, actor_id: str, *, limit: int = 50) -> list[AgentRun]:
        self._case_management.get_case(case_id, actor_id)
        return self._runs.list_for_case(case_id, limit=limit)

    def list_events(
        self,
        run_id: str,
        actor_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 200,
    ) -> list[RunEvent]:
        self._get_authorized_run(run_id, actor_id, write=False)
        return self._runs.list_events(
            run_id,
            after_sequence=after_sequence,
            limit=limit,
        )

    def get_evidence_plan(self, run_id: str, actor_id: str) -> EvidencePlan | None:
        run = self._get_authorized_run(run_id, actor_id, write=False)
        value = self._latest_checkpoint_state(run).get("evidence_plan")
        if value is None:
            return None
        return EvidencePlan.model_validate(value)

    def get_detail(self, run_id: str, actor_id: str) -> AssessmentRunDetail:
        run = self._get_authorized_run(run_id, actor_id, write=False)
        case = self._case_management.get_case(run.case_id, actor_id)
        membership = self._workspace_management.require_membership(
            run.workspace_id,
            actor_id,
        )
        can_write = membership.role in _WRITE_ROLES
        can_review_assignment = membership.role == "admin" or (
            membership.role == "reviewer"
            and (case.reviewer_id is None or case.reviewer_id == actor_id)
        )
        events = self._runs.list_events(run.run_id, limit=500)
        plan = self.get_evidence_plan(run.run_id, actor_id)
        facts = [
            FactDetail(
                fact=fact,
                evidence=self._facts.list_evidence(
                    fact.fact_id,
                    fact_version=fact.version,
                ),
            )
            for fact in self._facts.list_for_case(run.case_id)
        ]
        active_assessment = self._assessments.get_active(run.case_id, actor_id)
        if (
            active_assessment is not None
            and active_assessment.assessment.generated_by_run_id != run.run_id
        ):
            active_assessment = None
        tool_calls = _tool_call_details(events)
        return AssessmentRunDetail(
            run=run,
            duration_ms=_run_duration_ms(run),
            cost_currency=_cost_currency(run),
            timeline=_timeline(events),
            evidence_plan=plan,
            tool_calls=tool_calls,
            interrupt=(
                _latest_interrupt(events)
                if run.status in {"waiting_for_user", "waiting_for_review"}
                else None
            ),
            facts=facts,
            rule_evaluation=_latest_tool_output(
                tool_calls,
                "evaluate_deterministic_rules",
            ),
            citation_verification=_latest_tool_output(
                tool_calls,
                "verify_claim_citations",
            ),
            assessment=active_assessment,
            actions=RunActionCapabilities(
                can_continue=can_write
                and run.status in {"queued", "running", "waiting_for_user", "retrying"},
                can_retry=can_write and run.status == "failed",
                can_cancel=can_write
                and run.status
                in {"queued", "running", "waiting_for_user", "waiting_for_review", "retrying"},
                can_review=(
                    can_review_assignment
                    and run.status == "waiting_for_review"
                    and run.current_stage == "human_review"
                ),
            ),
        )

    def _generate_assessment_and_resume(
        self,
        run: AgentRun,
        actor_id: str,
        inspected: WorkflowExecutionResult,
    ) -> WorkflowExecutionResult:
        existing_assessment_id = inspected.state.get("assessment_id")
        if isinstance(existing_assessment_id, str) and existing_assessment_id:
            assessment_id = existing_assessment_id
        else:
            active = self._assessments.get_active(run.case_id, actor_id)
            if active is not None and active.assessment.generated_by_run_id == run.run_id:
                assessment_id = active.assessment.assessment_id
            else:
                bundle = self._assessments.generate(
                    run.case_id,
                    actor_id,
                    ruleset_version=_ruleset_version(inspected),
                    generated_by_run_id=run.run_id,
                )
                assessment_id = bundle.assessment.assessment_id
        return self._runtime.resume_case_assessment(
            thread_id=run.thread_id,
            resume_value={"assessment_id": assessment_id},
            actor_id=actor_id,
            actor_role=self._workspace_management.require_role(
                run.workspace_id,
                actor_id,
                _WRITE_ROLES,
                action="生成 Case Assessment",
            ).role,
        )

    def _handle_execution_result(
        self,
        run: AgentRun,
        result: WorkflowExecutionResult,
        *,
        actor_id: str,
    ) -> AgentRun:
        previous_state = self._latest_checkpoint_state(run)
        if (
            result.status == "interrupted"
            and result.interrupt is not None
            and result.interrupt.kind == "assessment_generation"
        ):
            generated = self._generate_assessment_and_resume(run, actor_id, result)
            result = _merge_execution_results(result, generated)

        now = max(time.time(), run.updated_at)
        if result.status == "completed":
            updated = run.complete(
                checkpoint_id=result.checkpoint_id,
                stage=result.stage,
                at=now,
            )
            event_type: RunEventType = "run_completed"
        elif result.interrupt is not None and result.interrupt.kind in {
            "assessment_review",
            "fact_conflict_review",
        }:
            updated = run.pause_for_review(
                checkpoint_id=result.checkpoint_id,
                stage=result.stage,
                at=now,
            )
            event_type = (
                "conflict_detected"
                if result.interrupt.kind == "fact_conflict_review"
                else "human_review_required"
            )
        else:
            updated = run.pause_for_user(
                checkpoint_id=result.checkpoint_id,
                stage=result.stage,
                at=now,
            )
            event_type = _pause_event_type(result)
        checkpoint = RunCheckpoint(
            checkpoint_id=_new_id("run_checkpoint"),
            run_id=run.run_id,
            thread_id=run.thread_id,
            version=updated.revision,
            stage=updated.current_stage,
            state=_checkpoint_state(result),
            created_at=now,
        )
        token_usage = max(run.token_usage, _token_usage(result))
        cost = max(run.cost, _estimated_cost(result, self._settings))
        updated = updated.model_copy(
            update={
                "checkpoint_id": checkpoint.checkpoint_id,
                "token_usage": token_usage,
                "cost": cost,
            }
        )
        events = _events_for_result(
            run_id=run.run_id,
            result=result,
            terminal_event_type=event_type,
            start_sequence=self._runs.next_event_sequence(run.run_id),
            created_at=now,
            previous_tool_trace_count=_tool_trace_count(previous_state),
            previous_node_trace_count=_node_trace_count(previous_state),
            plan_already_persisted="evidence_plan" in previous_state,
        )
        self._runs.save_progress(
            updated,
            checkpoint,
            events,
            expected_revision=run.revision,
        )
        return updated

    def _persist_running(self, run: AgentRun, *, ruleset_version: str) -> AgentRun:
        now = max(time.time(), run.updated_at)
        checkpoint = RunCheckpoint(
            checkpoint_id=_new_id("run_checkpoint"),
            run_id=run.run_id,
            thread_id=run.thread_id,
            version=run.revision + 1,
            stage="load_case",
            state={
                "case_id": run.case_id,
                "workspace_id": run.workspace_id,
                "ruleset_version": ruleset_version,
                "next_action": "invoke_langgraph",
            },
            created_at=now,
        )
        running = run.start(
            checkpoint_id=checkpoint.checkpoint_id,
            stage="load_case",
            at=now,
        )
        event = RunEvent(
            event_id=_new_id("run_event"),
            run_id=run.run_id,
            sequence=self._runs.next_event_sequence(run.run_id),
            event_type="stage_started",
            stage="load_case",
            created_at=now,
        )
        self._runs.save_progress(
            running,
            checkpoint,
            [event],
            expected_revision=run.revision,
        )
        return running

    def _persist_failure(self, run: AgentRun, exc: Exception) -> None:
        current = self._runs.get(run.run_id)
        if current is None or current.status in {"completed", "failed", "cancelled"}:
            return
        now = max(time.time(), current.updated_at)
        failed = current.fail(
            checkpoint_id=current.checkpoint_id or _new_id("run_checkpoint"),
            stage=current.current_stage,
            error_code=type(exc).__name__.upper(),
            error_message=_SAFE_RUN_FAILURE_MESSAGE,
            at=now,
        )
        checkpoint = RunCheckpoint(
            checkpoint_id=_new_id("run_checkpoint"),
            run_id=current.run_id,
            thread_id=current.thread_id,
            version=failed.revision,
            stage=failed.current_stage,
            state={
                **self._latest_checkpoint_state(current),
                "failed": True,
            },
            created_at=now,
        )
        failed = failed.model_copy(update={"checkpoint_id": checkpoint.checkpoint_id})
        event = RunEvent(
            event_id=_new_id("run_event"),
            run_id=current.run_id,
            sequence=self._runs.next_event_sequence(current.run_id),
            event_type="run_failed",
            stage=failed.current_stage,
            payload={"error_code": failed.error_code},
            created_at=now,
        )
        self._runs.save_progress(
            failed,
            checkpoint,
            [event],
            expected_revision=current.revision,
        )

    def _latest_checkpoint_state(self, run: AgentRun) -> dict[str, Any]:
        checkpoint = self._runs.get_latest_checkpoint(run.run_id)
        if checkpoint is None:
            return {
                "case_id": run.case_id,
                "workspace_id": run.workspace_id,
            }
        return dict(checkpoint.state)

    def _run_ruleset_version(self, run: AgentRun) -> str:
        value = self._latest_checkpoint_state(run).get("ruleset_version")
        if not isinstance(value, str) or not value:
            raise ValueError("Run checkpoint 缺少 ruleset_version")
        return value

    def _get_authorized_run(
        self,
        run_id: str,
        actor_id: str,
        *,
        write: bool,
    ) -> AgentRun:
        run = self._runs.get(run_id)
        if run is None:
            raise AgentRunNotFound(run_id)
        try:
            case = self._case_management.get_case(run.case_id, actor_id)
        except CaseNotFound as exc:
            raise AgentRunNotFound(run_id) from exc
        if write:
            self._workspace_management.require_role(
                case.workspace_id,
                actor_id,
                _WRITE_ROLES,
                action="继续 Case Assessment Run",
            )
        return run

    def _ensure_no_active_run(self, case_id: str) -> None:
        active = next(
            (
                run
                for run in self._runs.list_for_case(case_id, limit=50)
                if run.workflow_type == "case_assessment" and run.status in _ACTIVE_STATUSES
            ),
            None,
        )
        if active is not None:
            raise AgentRunAlreadyActive(case_id, active.run_id)

    def _document_readiness(self, case_id: str) -> CaseDocumentReadiness:
        documents = self._documents.list_for_case(case_id)
        return CaseDocumentReadiness(
            ready_document_ids=[
                document.document_id for document in documents if document.status == "ready"
            ],
            pending_document_ids=[
                document.document_id for document in documents if document.status != "ready"
            ],
        )

    def _missing_fact_fields(
        self,
        *,
        case_id: str,
        workspace_id: str,
        actor_id: str,
        jurisdiction: str,
        assessment_date: date,
        ruleset_version: str,
    ) -> list[str]:
        _, missing = self._fact_requirements(
            case_id=case_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            jurisdiction=jurisdiction,
            assessment_date=assessment_date,
            ruleset_version=ruleset_version,
        )
        return missing

    def _fact_requirements(
        self,
        *,
        case_id: str,
        workspace_id: str,
        actor_id: str,
        jurisdiction: str,
        assessment_date: date,
        ruleset_version: str,
    ) -> tuple[list[str], list[str]]:
        rules = self._policies.list_rules(
            workspace_id,
            actor_id,
            ruleset_version=ruleset_version,
            jurisdiction=jurisdiction,
            status="published",
        )
        applicable = [rule for rule in rules if rule.is_effective_on(assessment_date)]
        if not applicable:
            raise ValueError(
                f"规则集 {ruleset_version!r} 在评估日期 {assessment_date.isoformat()} 没有生效规则"
            )
        confirmed = {
            fact.field_name for fact in self._facts.list_for_case(case_id, statuses={"confirmed"})
        }
        required = {field_name for rule in applicable for field_name in rule.required_fact_fields}
        return sorted(required), sorted(required - confirmed)

    def _candidate_fact_ids(self, case_id: str) -> list[str]:
        return [
            fact.fact_id
            for fact in self._facts.list_for_case(
                case_id,
                statuses={"proposed", "conflicting"},
            )
        ]

    def _conflict_field_names(self, case_id: str) -> list[str]:
        return sorted(
            {
                fact.field_name
                for fact in self._facts.list_for_case(
                    case_id,
                    statuses={"conflicting"},
                )
            }
        )

    def _require_published_rules(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        jurisdiction: str,
        assessment_date: date,
        ruleset_version: str,
    ) -> None:
        rules = self._policies.list_rules(
            workspace_id,
            actor_id,
            ruleset_version=ruleset_version,
            jurisdiction=jurisdiction,
            status="published",
        )
        if not rules:
            raise ValueError(f"规则集 {ruleset_version!r} 在当前 Workspace 和法域下没有已发布规则")
        if not any(rule.is_effective_on(assessment_date) for rule in rules):
            raise ValueError(
                f"规则集 {ruleset_version!r} 在评估日期 {assessment_date.isoformat()} 没有生效规则"
            )


def _ruleset_version(result: WorkflowExecutionResult) -> str:
    value = result.state.get("ruleset_version")
    if not isinstance(value, str) or not value:
        raise ValueError("工作流状态缺少 ruleset_version")
    return value


def _require_assessment_date(value: date | None) -> date:
    if value is None:
        raise ValueError("案件缺少 assessment_date，无法恢复 Assessment Run")
    return value


def _pause_event_type(result: WorkflowExecutionResult) -> RunEventType:
    if result.interrupt is None:
        return "run_paused"
    if result.interrupt.kind == "fact_confirmation":
        return "fact_confirmation_required"
    if result.interrupt.kind == "fact_conflict_review":
        return "conflict_detected"
    return "human_input_required"


def _checkpoint_state(result: WorkflowExecutionResult) -> dict[str, Any]:
    state: dict[str, Any] = {
        "case_id": result.state.get("case_id"),
        "workspace_id": result.state.get("workspace_id"),
        "ruleset_version": result.state.get("ruleset_version"),
        "langgraph_checkpoint_id": result.checkpoint_id,
        "stage": result.stage,
    }
    if result.interrupt is not None:
        state["interrupt_kind"] = result.interrupt.kind
        state["interrupt_payload"] = result.interrupt.payload
    assessment_id = result.state.get("assessment_id")
    if assessment_id is not None:
        state["assessment_id"] = assessment_id
    for field_name in (
        "evidence_plan",
        "evidence_query_count",
        "case_evidence_ids",
        "regulation_rule_ids",
        "candidate_fact_ids",
        "conflict_field_names",
        "refusal_reason",
        "budget",
        "tool_trace",
        "node_trace",
    ):
        if field_name in result.state:
            state[field_name] = result.state[field_name]
    return state


def _merge_execution_results(
    before: WorkflowExecutionResult,
    after: WorkflowExecutionResult,
) -> WorkflowExecutionResult:
    completed_stages = [
        *before.completed_stages,
        *(stage for stage in after.completed_stages if stage not in before.completed_stages),
    ]
    return WorkflowExecutionResult(
        status=after.status,
        checkpoint_id=after.checkpoint_id,
        stage=after.stage,
        state=after.state,
        completed_stages=completed_stages,
        interrupt=after.interrupt,
    )


def _events_for_result(
    *,
    run_id: str,
    result: WorkflowExecutionResult,
    terminal_event_type: RunEventType,
    start_sequence: int,
    created_at: float,
    previous_tool_trace_count: int,
    previous_node_trace_count: int,
    plan_already_persisted: bool,
) -> list[RunEvent]:
    events: list[RunEvent] = []
    sequence = start_sequence
    node_trace = result.state.get("node_trace", [])
    new_node_trace = node_trace[previous_node_trace_count:] if isinstance(node_trace, list) else []
    if new_node_trace:
        for trace in new_node_trace:
            if not isinstance(trace, dict):
                continue
            stage = _optional_string(trace.get("stage"))
            if stage is None:
                continue
            events.append(
                RunEvent(
                    event_id=_new_id("run_event"),
                    run_id=run_id,
                    sequence=sequence,
                    event_type="stage_completed",
                    stage=stage,
                    payload={
                        "status": _safe_string(trace.get("status"), default="completed"),
                        "duration_ms": _safe_non_negative_int(trace.get("duration_ms")),
                    },
                    created_at=created_at,
                )
            )
            sequence += 1
    else:
        for stage in result.completed_stages:
            events.append(
                RunEvent(
                    event_id=_new_id("run_event"),
                    run_id=run_id,
                    sequence=sequence,
                    event_type="stage_completed",
                    stage=stage,
                    payload={"status": "completed", "duration_ms": 0},
                    created_at=created_at,
                )
            )
            sequence += 1
    evidence_plan = result.state.get("evidence_plan")
    if not plan_already_persisted and isinstance(evidence_plan, dict):
        events.append(
            RunEvent(
                event_id=_new_id("run_event"),
                run_id=run_id,
                sequence=sequence,
                event_type="stage_progress",
                stage="build_evidence_plan",
                payload={"evidence_plan": evidence_plan},
                created_at=created_at,
            )
        )
        sequence += 1
    tool_trace = result.state.get("tool_trace", [])
    if isinstance(tool_trace, list):
        for trace in tool_trace[previous_tool_trace_count:]:
            if not isinstance(trace, dict):
                continue
            events.append(
                RunEvent(
                    event_id=_new_id("run_event"),
                    run_id=run_id,
                    sequence=sequence,
                    event_type="tool_completed",
                    stage=_optional_string(trace.get("stage")),
                    payload={
                        "tool_name": trace.get("tool_name"),
                        "arguments": trace.get("arguments", {}),
                        "result_summary": trace.get("result_summary", ""),
                        "duration_ms": trace.get("duration_ms", 0),
                        "retry_count": trace.get("retry_count", 0),
                        "token_usage": trace.get("token_usage", 0),
                        "output": trace.get("output", {}),
                    },
                    created_at=created_at,
                )
            )
            sequence += 1
    candidate_fact_ids = result.state.get("candidate_fact_ids", [])
    if (
        isinstance(candidate_fact_ids, list)
        and candidate_fact_ids
        and previous_tool_trace_count < len(tool_trace)
    ):
        events.append(
            RunEvent(
                event_id=_new_id("run_event"),
                run_id=run_id,
                sequence=sequence,
                event_type="facts_proposed",
                stage="extract_fact_candidates",
                payload={"fact_ids": candidate_fact_ids},
                created_at=created_at,
            )
        )
        sequence += 1
    payload: dict[str, Any] = {}
    if result.interrupt is not None:
        payload = {
            "interrupt_kind": result.interrupt.kind,
            **result.interrupt.payload,
        }
    events.append(
        RunEvent(
            event_id=_new_id("run_event"),
            run_id=run_id,
            sequence=sequence,
            event_type=terminal_event_type,
            stage=result.stage,
            payload=payload,
            created_at=created_at,
        )
    )
    return events


def _tool_trace_count(state: dict[str, Any]) -> int:
    value = state.get("tool_trace", [])
    return len(value) if isinstance(value, list) else 0


def _node_trace_count(state: dict[str, Any]) -> int:
    value = state.get("node_trace", [])
    return len(value) if isinstance(value, list) else 0


_TOOL_OUTPUT_FIELDS: dict[str, frozenset[str]] = {
    "retrieve_case_evidence": frozenset(
        {"evidence_ids", "document_ids", "document_version_ids", "hit_count"}
    ),
    "retrieve_regulations": frozenset({"rule_ids", "required_fact_fields", "source_clause_ids"}),
    "extract_fact_candidates": frozenset(
        {"fact_ids", "proposed_field_names", "conflict_field_names"}
    ),
    "evaluate_deterministic_rules": frozenset(
        {"triggered_rule_ids", "missing_fact_fields", "evaluation_status_by_rule"}
    ),
    "verify_claim_citations": frozenset(
        {"assessment_id", "valid", "citation_count", "finding_count"}
    ),
}
_TOOL_ARGUMENT_FIELDS: dict[str, frozenset[str]] = {
    "retrieve_case_evidence": frozenset({"query", "query_length", "top_k"}),
    "retrieve_regulations": frozenset({"ruleset_version"}),
    "extract_fact_candidates": frozenset({"field_names", "document_ids"}),
    "evaluate_deterministic_rules": frozenset({"ruleset_version"}),
    "verify_claim_citations": frozenset({"assessment_id"}),
}
_INTERRUPT_EVENTS = {
    "fact_confirmation_required",
    "conflict_detected",
    "human_input_required",
    "human_review_required",
    "run_paused",
}
_EVENT_SUMMARIES = {
    "run_started": "Run 已创建",
    "stage_started": "工作流开始执行",
    "stage_progress": "证据计划已生成",
    "stage_completed": "节点执行完成",
    "tool_completed": "工具调用完成",
    "facts_proposed": "已生成事实候选",
    "fact_confirmation_required": "等待人工确认关键事实",
    "conflict_detected": "检测到事实冲突，等待 Reviewer",
    "human_input_required": "等待人工输入",
    "human_review_required": "等待 Reviewer 审批",
    "run_retrying": "Run 正在重试",
    "run_failed": "Run 执行失败",
    "run_completed": "Run 已完成",
    "run_cancelled": "Run 已取消",
}


def _timeline(events: list[RunEvent]) -> list[RunTimelineItem]:
    return [
        RunTimelineItem(
            sequence=event.sequence,
            event_type=event.event_type,
            stage=event.stage,
            status=_event_status(event),
            summary=_EVENT_SUMMARIES.get(event.event_type, event.event_type),
            duration_ms=_safe_non_negative_int(event.payload.get("duration_ms")),
            created_at=event.created_at,
        )
        for event in events
    ]


def _tool_call_details(events: list[RunEvent]) -> list[RunToolCallDetail]:
    details: list[RunToolCallDetail] = []
    for event in events:
        if event.event_type != "tool_completed":
            continue
        tool_name = _safe_string(event.payload.get("tool_name"))
        if not tool_name:
            continue
        arguments = event.payload.get("arguments")
        output = event.payload.get("output")
        details.append(
            RunToolCallDetail(
                sequence=event.sequence,
                tool_name=tool_name,
                stage=event.stage,
                arguments=_safe_tool_arguments(
                    tool_name,
                    dict(arguments) if isinstance(arguments, dict) else {},
                ),
                result_summary=_safe_string(event.payload.get("result_summary")),
                output=_safe_tool_output(
                    tool_name,
                    dict(output) if isinstance(output, dict) else {},
                ),
                duration_ms=_safe_non_negative_int(event.payload.get("duration_ms")),
                retry_count=_safe_non_negative_int(event.payload.get("retry_count")),
                token_usage=_safe_non_negative_int(event.payload.get("token_usage")),
                created_at=event.created_at,
            )
        )
    return details


def _latest_interrupt(events: list[RunEvent]) -> RunInterruptDetail | None:
    for event in reversed(events):
        if event.event_type not in _INTERRUPT_EVENTS:
            continue
        payload = event.payload
        kind = _safe_string(payload.get("interrupt_kind"), default=event.event_type)
        return RunInterruptDetail(
            kind=kind,
            stage=event.stage,
            reason=_EVENT_SUMMARIES.get(event.event_type, "等待人工处理"),
            missing_fact_fields=_safe_string_list(payload.get("missing_fact_fields")),
            conflict_field_names=_safe_string_list(payload.get("conflict_field_names")),
            candidate_fact_ids=_safe_string_list(payload.get("candidate_fact_ids")),
            created_at=event.created_at,
        )
    return None


def _latest_tool_output(
    tool_calls: list[RunToolCallDetail],
    tool_name: str,
) -> dict[str, Any] | None:
    for item in reversed(tool_calls):
        if item.tool_name == tool_name:
            return dict(item.output)
    return None


def _safe_tool_output(tool_name: str, output: dict[str, Any]) -> dict[str, Any]:
    allowed = _TOOL_OUTPUT_FIELDS.get(tool_name, frozenset())
    return {key: value for key, value in output.items() if key in allowed}


def _safe_tool_arguments(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    allowed = _TOOL_ARGUMENT_FIELDS.get(tool_name, frozenset())
    return {key: value for key, value in arguments.items() if key in allowed}


def _event_status(event: RunEvent) -> str:
    if event.event_type in {"run_failed"}:
        return "failed"
    if event.event_type in {"run_completed"}:
        return "completed"
    if event.event_type in {"run_cancelled"}:
        return "cancelled"
    if event.event_type in _INTERRUPT_EVENTS:
        return "interrupted"
    return _safe_string(event.payload.get("status"), default="completed")


def _run_duration_ms(run: AgentRun) -> int:
    if run.started_at is None:
        return 0
    end = run.completed_at if run.completed_at is not None else run.updated_at
    return max(0, int((end - run.started_at) * 1000))


def _cost_currency(run: AgentRun) -> str:
    return _safe_string(
        run.model_config_snapshot.get("cost_currency"),
        default="unspecified",
    )


def _safe_string(value: Any, *, default: str = "") -> str:
    return value if isinstance(value, str) and value else default


def _safe_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(item for item in value if isinstance(item, str) and item))


def _safe_non_negative_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _token_usage(result: WorkflowExecutionResult) -> int:
    budget = result.state.get("budget", {})
    if not isinstance(budget, dict):
        return 0
    value = budget.get("token_usage", 0)
    return value if isinstance(value, int) and value >= 0 else 0


def _estimated_cost(result: WorkflowExecutionResult, settings: Settings | None) -> float:
    if settings is None:
        return 0.0
    budget = result.state.get("budget", {})
    if not isinstance(budget, dict):
        return 0.0
    input_tokens = budget.get("input_tokens", 0)
    output_tokens = budget.get("output_tokens", 0)
    if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
        return 0.0
    return (
        input_tokens * settings.llm_input_cost_per_1m_tokens
        + output_tokens * settings.llm_output_cost_per_1m_tokens
    ) / 1_000_000


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
