"""V3 Evidence QA 路由。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends

from api.v2.deps import make_require_owner
from api.v3.schemas import EvidenceQARequest, EvidenceQAResponse

if TYPE_CHECKING:
    from app.container import AppContainer
    from domain.qa import EvidenceQAAnswer


def _to_response(result: EvidenceQAAnswer) -> EvidenceQAResponse:
    return EvidenceQAResponse(
        question=result.question,
        scope=result.scope.model_dump(),
        status=result.status,
        answer=result.answer,
        claims=[claim.model_dump() for claim in result.claims],
        citations=[citation.model_dump() for citation in result.citations],
        refusal_reason=result.refusal_reason,
        unanswered_aspects=result.unanswered_aspects,
        verification=result.verification.model_dump(),
        support_verification=result.support_verification.model_dump(),
        repair_report=result.repair_report.model_dump(),
    )


def build_qa_routes(container: AppContainer) -> APIRouter:
    router = APIRouter(tags=["v3-qa"])
    require_owner = make_require_owner(container)

    @router.post(
        "/qa",
        response_model=EvidenceQAResponse,
        summary="在授权语料范围内执行带 Claim-Citation 校验的简单问答",
    )
    def answer_evidence_qa(
        body: EvidenceQARequest,
        actor_id: str = Depends(require_owner),
    ) -> EvidenceQAResponse:
        result = container.evidence_qa.answer(
            actor_id,
            question=body.question,
            corpora=body.corpora,
            workspace_id=body.workspace_id,
            case_id=body.case_id,
            assessment_id=body.assessment_id,
            top_k=body.top_k,
        )
        return _to_response(result)

    return router
