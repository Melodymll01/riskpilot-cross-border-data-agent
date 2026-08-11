"""V3 Assessment 生成、查询与人工审批路由。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, status

from api.v2.deps import make_require_owner
from api.v3.schemas import (
    ActionItemOut,
    AssessmentBundleResponse,
    AssessmentEvidenceCitationOut,
    AssessmentListResponse,
    AssessmentOut,
    FindingOut,
    GenerateAssessmentRequest,
    PolicyEvaluationOut,
    ReviewAssessmentRequest,
)

if TYPE_CHECKING:
    from app.container import AppContainer
    from domain.assessments import (
        ActionItem,
        Assessment,
        AssessmentBundle,
        AssessmentEvidenceCitation,
        Finding,
    )


def _to_assessment_out(assessment: Assessment) -> AssessmentOut:
    return AssessmentOut(
        assessment_id=assessment.assessment_id,
        case_id=assessment.case_id,
        version=assessment.version,
        status=assessment.status,
        assessment_date=assessment.assessment_date,
        jurisdiction=assessment.jurisdiction,
        ruleset_version=assessment.ruleset_version,
        fact_versions=assessment.fact_versions,
        policy_evaluations=[
            PolicyEvaluationOut(**item.model_dump()) for item in assessment.policy_evaluations
        ],
        risk_level=assessment.risk_level,
        candidate_paths=assessment.candidate_paths,
        generated_by_run_id=assessment.generated_by_run_id,
        approved_by=assessment.approved_by,
        approved_at=assessment.approved_at,
        review_comment=assessment.review_comment,
        created_at=assessment.created_at,
        updated_at=assessment.updated_at,
    )


def _to_finding_out(finding: Finding) -> FindingOut:
    return FindingOut(**finding.model_dump())


def _to_action_out(action: ActionItem) -> ActionItemOut:
    return ActionItemOut(**action.model_dump())


def _to_evidence_citation_out(
    citation: AssessmentEvidenceCitation,
) -> AssessmentEvidenceCitationOut:
    return AssessmentEvidenceCitationOut(**citation.model_dump(exclude={"assessment_id"}))


def _to_bundle_out(bundle: AssessmentBundle) -> AssessmentBundleResponse:
    return AssessmentBundleResponse(
        assessment=_to_assessment_out(bundle.assessment),
        findings=[_to_finding_out(item) for item in bundle.findings],
        action_items=[_to_action_out(item) for item in bundle.action_items],
        evidence_citations=[_to_evidence_citation_out(item) for item in bundle.evidence_citations],
    )


def build_assessment_routes(container: AppContainer) -> APIRouter:
    router = APIRouter(tags=["v3-assessments"])
    require_owner = make_require_owner(container)

    @router.post(
        "/cases/{case_id}/assessments",
        response_model=AssessmentBundleResponse,
        status_code=status.HTTP_201_CREATED,
        summary="从 confirmed 事实和已发布规则生成 Assessment",
    )
    def generate_assessment(
        case_id: str,
        body: GenerateAssessmentRequest,
        actor_id: str = Depends(require_owner),
    ) -> AssessmentBundleResponse:
        bundle = container.assessment_management.generate(
            case_id,
            actor_id,
            ruleset_version=body.ruleset_version,
            generated_by_run_id=body.generated_by_run_id,
        )
        return _to_bundle_out(bundle)

    @router.get(
        "/cases/{case_id}/assessments/active",
        response_model=AssessmentBundleResponse,
        summary="获取案件活动 Assessment",
    )
    def get_active_assessment(
        case_id: str,
        actor_id: str = Depends(require_owner),
    ) -> AssessmentBundleResponse:
        bundle = container.assessment_management.get_active(case_id, actor_id)
        if bundle is None:
            from domain.errors import AssessmentNotFound

            raise AssessmentNotFound(f"active:{case_id}")
        return _to_bundle_out(bundle)

    @router.get(
        "/cases/{case_id}/assessments",
        response_model=AssessmentListResponse,
        summary="列出案件 Assessment 版本",
    )
    def list_assessment_versions(
        case_id: str,
        actor_id: str = Depends(require_owner),
    ) -> AssessmentListResponse:
        assessments = container.assessment_management.list_versions(case_id, actor_id)
        return AssessmentListResponse(
            assessments=[_to_assessment_out(item) for item in assessments]
        )

    @router.get(
        "/assessments/{assessment_id}",
        response_model=AssessmentBundleResponse,
        summary="获取指定 Assessment 版本",
    )
    def get_assessment(
        assessment_id: str,
        actor_id: str = Depends(require_owner),
    ) -> AssessmentBundleResponse:
        return _to_bundle_out(container.assessment_management.get(assessment_id, actor_id))

    @router.post(
        "/assessments/{assessment_id}/review",
        response_model=AssessmentBundleResponse,
        summary="审批当前活动 Assessment（Reviewer/Admin）",
    )
    def review_assessment(
        assessment_id: str,
        body: ReviewAssessmentRequest,
        actor_id: str = Depends(require_owner),
    ) -> AssessmentBundleResponse:
        bundle = container.assessment_management.review(
            assessment_id,
            actor_id,
            decision=body.decision,
            comment=body.comment,
        )
        return _to_bundle_out(bundle)

    return router
