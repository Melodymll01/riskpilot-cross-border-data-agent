"""V3 合规案件资源路由。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query, status

from api.v2.deps import make_require_owner
from api.v3.schemas import (
    CaseListResponse,
    CaseOut,
    CreateCaseRequest,
    TransitionCaseRequest,
    UpdateCaseRequest,
)

if TYPE_CHECKING:
    from app.container import AppContainer
    from domain.cases import Case


def _to_case_out(case: Case) -> CaseOut:
    return CaseOut(
        case_id=case.case_id,
        workspace_id=case.workspace_id,
        title=case.title,
        description=case.description,
        jurisdiction=case.jurisdiction,
        scenario_type=case.scenario_type,
        assessment_date=case.assessment_date,
        status=case.status,
        owner_id=case.owner_id,
        reviewer_id=case.reviewer_id,
        active_assessment_id=case.active_assessment_id,
        created_at=case.created_at,
        updated_at=case.updated_at,
    )


def build_case_routes(container: AppContainer) -> APIRouter:
    router = APIRouter(prefix="/cases", tags=["v3-cases"])
    require_owner = make_require_owner(container)

    @router.post(
        "",
        response_model=CaseOut,
        status_code=status.HTTP_201_CREATED,
        summary="在 Workspace 中创建合规案件",
    )
    def create_case(
        body: CreateCaseRequest,
        actor_id: str = Depends(require_owner),
    ) -> CaseOut:
        case = container.case_management.create_case(
            actor_id,
            workspace_id=body.workspace_id,
            title=body.title,
            description=body.description,
            jurisdiction=body.jurisdiction,
            scenario_type=body.scenario_type,
            assessment_date=body.assessment_date,
            reviewer_id=body.reviewer_id,
        )
        return _to_case_out(case)

    @router.get(
        "",
        response_model=CaseListResponse,
        summary="列出 Workspace 中当前用户可见的案件",
    )
    def list_cases(
        workspace_id: str = Query(min_length=1),
        include_archived: bool = False,
        limit: int = Query(default=50, ge=1, le=200),
        actor_id: str = Depends(require_owner),
    ) -> CaseListResponse:
        cases = container.case_management.list_cases(
            actor_id,
            workspace_id=workspace_id,
            include_archived=include_archived,
            limit=limit,
        )
        return CaseListResponse(cases=[_to_case_out(case) for case in cases])

    @router.get(
        "/{case_id}",
        response_model=CaseOut,
        summary="获取当前用户可见的案件",
    )
    def get_case(
        case_id: str,
        actor_id: str = Depends(require_owner),
    ) -> CaseOut:
        return _to_case_out(container.case_management.get_case(case_id, actor_id))

    @router.patch(
        "/{case_id}",
        response_model=CaseOut,
        summary="更新案件基本资料",
    )
    def update_case(
        case_id: str,
        body: UpdateCaseRequest,
        actor_id: str = Depends(require_owner),
    ) -> CaseOut:
        changes = {field_name: getattr(body, field_name) for field_name in body.model_fields_set}
        case = container.case_management.update_case(
            case_id,
            actor_id,
            changes=changes,
        )
        return _to_case_out(case)

    @router.post(
        "/{case_id}/transitions",
        response_model=CaseOut,
        summary="按领域状态机转换案件状态",
    )
    def transition_case(
        case_id: str,
        body: TransitionCaseRequest,
        actor_id: str = Depends(require_owner),
    ) -> CaseOut:
        case = container.case_management.transition_case(
            case_id,
            actor_id,
            body.target,
        )
        return _to_case_out(case)

    return router
