"""V2 Case Assessment LangGraph 运行编排用例。"""

from __future__ import annotations

import time
import uuid
from datetime import date
from typing import TYPE_CHECKING, Any, cast

from domain.errors import (
    AgentRunAlreadyActive,
    AgentRunConflict,
    AgentRunNotFound,
    CaseNotFound,
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
    from domain.assessments import AssessmentStatus
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
    ) -> None:
        self._runs = run_repo
        self._runtime = workflow_runtime
        self._documents = document_repo
        self._facts = fact_repo
        self._case_management = case_management
        self._workspace_management = workspace_management
        self._policies = policy_management
        self._assessments = assessment_management

    def start(
        self,
        case_id: str,
        actor_id: str,
        *,
        ruleset_version: str,
        model_config_snapshot: dict[str, Any] | None = None,
    ) -> AgentRun:
        case = self._case_management.get_case(case_id, actor_id)
        self._workspace_management.require_role(
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
        missing_fields = self._missing_fact_fields(
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
        run = AgentRun(
            run_id=run_id,
            workspace_id=case.workspace_id,
            case_id=case.case_id,
            workflow_type="case_assessment",
            thread_id=thread_id,
            checkpoint_id=initial_checkpoint_id,
            current_stage="queued",
            model_config_snapshot=model_config_snapshot or {},
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
                ruleset_version=ruleset_version,
                document_readiness=readiness,
                missing_fact_fields=missing_fields,
            )
            return self._handle_execution_result(running, result, actor_id=actor_id)
        except Exception as exc:
            self._persist_failure(running, exc)
            raise

    def continue_run(self, run_id: str, actor_id: str) -> AgentRun:
        run = self._get_authorized_run(run_id, actor_id, write=True)
        if run.status not in {"waiting_for_user", "retrying", "running"}:
            raise ValueError("只有等待用户、重试中或待对账的 Run 可以继续")
        try:
            return self._continue_run(run, actor_id)
        except AgentRunConflict:
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
            ruleset_version = self._run_ruleset_version(run)
            result = self._runtime.start_case_assessment(
                thread_id=run.thread_id,
                case_id=case.case_id,
                workspace_id=case.workspace_id,
                actor_id=actor_id,
                ruleset_version=ruleset_version,
                document_readiness=self._document_readiness(case.case_id),
                missing_fact_fields=self._missing_fact_fields(
                    case_id=case.case_id,
                    workspace_id=case.workspace_id,
                    actor_id=actor_id,
                    jurisdiction=case.jurisdiction,
                    assessment_date=case.assessment_date,
                    ruleset_version=ruleset_version,
                ),
            )
            return self._handle_execution_result(run, result, actor_id=actor_id)
        if run.status == "running":
            return self._handle_execution_result(run, inspected, actor_id=actor_id)

        case = self._case_management.get_case(run.case_id, actor_id)
        if inspected.interrupt is None:
            return self._handle_execution_result(run, inspected, actor_id=actor_id)
        kind = inspected.interrupt.kind
        if kind == "documents_required":
            readiness = self._document_readiness(run.case_id)
            if readiness.blocked:
                return self._handle_execution_result(
                    run,
                    inspected,
                    actor_id=actor_id,
                )
            result = self._runtime.resume_case_assessment(
                thread_id=run.thread_id,
                resume_value={"action": "retry"},
                state_update=readiness.model_dump(),
            )
        elif kind == "fact_confirmation":
            missing_fields = self._missing_fact_fields(
                case_id=case.case_id,
                workspace_id=case.workspace_id,
                actor_id=actor_id,
                jurisdiction=case.jurisdiction,
                assessment_date=case.assessment_date,
                ruleset_version=_ruleset_version(inspected),
            )
            if missing_fields:
                return self._handle_execution_result(
                    run,
                    inspected,
                    actor_id=actor_id,
                )
            result = self._runtime.resume_case_assessment(
                thread_id=run.thread_id,
                resume_value={"action": "retry"},
                state_update={"missing_fact_fields": missing_fields},
            )
        elif kind == "assessment_generation":
            result = self._generate_assessment_and_resume(run, actor_id, inspected)
        else:
            return self._handle_execution_result(run, inspected, actor_id=actor_id)
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
            result = self._runtime.resume_case_assessment(
                thread_id=run.thread_id,
                resume_value={"decision": reviewed.assessment.status},
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
        return cast(
            "list[AgentRun]",
            self._runs.list_for_case(case_id, limit=limit),
        )

    def list_events(
        self,
        run_id: str,
        actor_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 200,
    ) -> list[RunEvent]:
        self._get_authorized_run(run_id, actor_id, write=False)
        return cast(
            "list[RunEvent]",
            self._runs.list_events(
                run_id,
                after_sequence=after_sequence,
                limit=limit,
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
        )

    def _handle_execution_result(
        self,
        run: AgentRun,
        result: WorkflowExecutionResult,
        *,
        actor_id: str,
    ) -> AgentRun:
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
        elif result.interrupt is not None and result.interrupt.kind == "assessment_review":
            updated = run.pause_for_review(
                checkpoint_id=result.checkpoint_id,
                stage=result.stage,
                at=now,
            )
            event_type = "human_review_required"
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
        updated = cast(
            "AgentRun",
            updated.model_copy(update={"checkpoint_id": checkpoint.checkpoint_id}),
        )
        events = _events_for_result(
            run_id=run.run_id,
            result=result,
            terminal_event_type=event_type,
            start_sequence=self._runs.next_event_sequence(run.run_id),
            created_at=now,
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
            error_message=str(exc)[:2000],
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
        return sorted(required - confirmed)

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


def _pause_event_type(result: WorkflowExecutionResult) -> RunEventType:
    if result.interrupt is None:
        return "run_paused"
    if result.interrupt.kind == "fact_confirmation":
        return "fact_confirmation_required"
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
) -> list[RunEvent]:
    events: list[RunEvent] = []
    sequence = start_sequence
    for stage in result.completed_stages:
        events.append(
            RunEvent(
                event_id=_new_id("run_event"),
                run_id=run_id,
                sequence=sequence,
                event_type="stage_completed",
                stage=stage,
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
