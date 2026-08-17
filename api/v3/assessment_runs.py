"""V3 Case Assessment Run 生命周期路由。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query, status
from fastapi.exceptions import HTTPException

from api.v2.deps import make_require_owner
from api.v3.schemas import (
    AgentRunListResponse,
    AgentRunOut,
    EvidencePlanOut,
    ReviewAssessmentRunRequest,
    RunEventListResponse,
    RunEventOut,
    StartAssessmentRunRequest,
)

if TYPE_CHECKING:
    from app.container import AppContainer
    from domain.runs import AgentRun, RunEvent


def _to_run_out(run: AgentRun) -> AgentRunOut:
    return AgentRunOut(
        run_id=run.run_id,
        workspace_id=run.workspace_id,
        case_id=run.case_id,
        workflow_type=run.workflow_type,
        status=run.status,
        current_stage=run.current_stage,
        checkpoint_id=run.checkpoint_id,
        token_usage=run.token_usage,
        cost=run.cost,
        retry_count=run.retry_count,
        revision=run.revision,
        created_by=run.created_by,
        error_code=run.error_code,
        error_message=run.error_message,
        created_at=run.created_at,
        updated_at=run.updated_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
    )


def _to_event_out(event: RunEvent) -> RunEventOut:
    return RunEventOut(**event.model_dump())


def build_assessment_run_routes(container: AppContainer) -> APIRouter:
    router = APIRouter(tags=["v3-assessment-runs"])
    require_owner = make_require_owner(container)

    @router.post(
        "/cases/{case_id}/assessment-runs",
        response_model=AgentRunOut,
        status_code=status.HTTP_201_CREATED,
        summary="启动可恢复的 LangGraph 案件评估",
    )
    def start_assessment_run(
        case_id: str,
        body: StartAssessmentRunRequest,
        actor_id: str = Depends(require_owner),
    ) -> AgentRunOut:
        run = container.assessment_runs.start(
            case_id,
            actor_id,
            ruleset_version=body.ruleset_version,
            model_config_snapshot=body.model_config_snapshot,
        )
        return _to_run_out(run)

    @router.get(
        "/cases/{case_id}/assessment-runs",
        response_model=AgentRunListResponse,
        summary="列出案件评估运行",
    )
    def list_assessment_runs(
        case_id: str,
        limit: int = Query(default=50, ge=1, le=200),
        actor_id: str = Depends(require_owner),
    ) -> AgentRunListResponse:
        runs = container.assessment_runs.list_for_case(
            case_id,
            actor_id,
            limit=limit,
        )
        return AgentRunListResponse(runs=[_to_run_out(item) for item in runs])

    @router.get(
        "/runs/{run_id}",
        response_model=AgentRunOut,
        summary="获取工作流运行状态",
    )
    def get_assessment_run(
        run_id: str,
        actor_id: str = Depends(require_owner),
    ) -> AgentRunOut:
        return _to_run_out(container.assessment_runs.get(run_id, actor_id))

    @router.get(
        "/runs/{run_id}/events",
        response_model=RunEventListResponse,
        summary="增量读取工作流阶段事件",
    )
    def list_run_events(
        run_id: str,
        after_sequence: int = Query(default=0, ge=0),
        limit: int = Query(default=200, ge=1, le=500),
        actor_id: str = Depends(require_owner),
    ) -> RunEventListResponse:
        events = container.assessment_runs.list_events(
            run_id,
            actor_id,
            after_sequence=after_sequence,
            limit=limit,
        )
        return RunEventListResponse(events=[_to_event_out(item) for item in events])

    @router.get(
        "/runs/{run_id}/plan",
        response_model=EvidencePlanOut,
        summary="获取结构化证据计划",
    )
    def get_evidence_plan(
        run_id: str,
        actor_id: str = Depends(require_owner),
    ) -> EvidencePlanOut:
        plan = container.assessment_runs.get_evidence_plan(run_id, actor_id)
        if plan is None:
            raise HTTPException(status_code=409, detail="EvidencePlan 尚未生成")
        return EvidencePlanOut(**plan.model_dump())

    @router.post(
        "/runs/{run_id}/continue",
        response_model=AgentRunOut,
        summary="重新检查材料和事实并继续运行",
    )
    def continue_assessment_run(
        run_id: str,
        actor_id: str = Depends(require_owner),
    ) -> AgentRunOut:
        return _to_run_out(container.assessment_runs.continue_run(run_id, actor_id))

    @router.post(
        "/runs/{run_id}/retry",
        response_model=AgentRunOut,
        summary="重试 failed 工作流运行",
    )
    def retry_assessment_run(
        run_id: str,
        actor_id: str = Depends(require_owner),
    ) -> AgentRunOut:
        return _to_run_out(container.assessment_runs.retry_run(run_id, actor_id))

    @router.post(
        "/runs/{run_id}/cancel",
        response_model=AgentRunOut,
        summary="取消非终态工作流运行",
    )
    def cancel_assessment_run(
        run_id: str,
        actor_id: str = Depends(require_owner),
    ) -> AgentRunOut:
        return _to_run_out(container.assessment_runs.cancel_run(run_id, actor_id))

    @router.post(
        "/runs/{run_id}/review",
        response_model=AgentRunOut,
        summary="审批运行生成的活动 Assessment（Reviewer/Admin）",
    )
    def review_assessment_run(
        run_id: str,
        body: ReviewAssessmentRunRequest,
        actor_id: str = Depends(require_owner),
    ) -> AgentRunOut:
        run = container.assessment_runs.review_run(
            run_id,
            actor_id,
            decision=body.decision,
            comment=body.comment,
        )
        return _to_run_out(run)

    return router
