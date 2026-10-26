"""Authorized evidence QA; independent from the conversational answer route."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from api.v2.deps import make_require_owner
from domain.qa import (
    ClaimCitationVerification,
    ClaimRepairReport,
    ClaimSupportResult,
    EvidenceQACitation,
    EvidenceQAClaim,
    EvidenceQAPresentation,
    EvidenceQAScope,
    EvidenceQAStatus,
)

if TYPE_CHECKING:
    from app.container import AppContainer


class EvidenceQARequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=2000)
    corpora: list[Literal["regulatory", "user_docs"]] = Field(
        default_factory=lambda: ["regulatory"], min_length=1
    )
    document_ids: list[str] | None = None
    top_k: int = Field(5, ge=1, le=20)


class EvidenceQAResponse(BaseModel):
    question: str
    scope: EvidenceQAScope
    status: EvidenceQAStatus
    answer: str
    claims: list[EvidenceQAClaim]
    citations: list[EvidenceQACitation]
    refusal_reason: str
    unanswered_aspects: list[str]
    presentation: EvidenceQAPresentation | None
    verification: ClaimCitationVerification
    support_verification: ClaimSupportResult
    repair_report: ClaimRepairReport


def build_qa_routes(container: AppContainer) -> APIRouter:
    router = APIRouter(tags=["evidence-qa"])

    @router.post("/qa", response_model=EvidenceQAResponse)
    def answer(
        body: EvidenceQARequest, owner_id: str = Depends(make_require_owner(container))
    ) -> EvidenceQAResponse:
        result = container.evidence_qa.answer(owner_id, **body.model_dump())
        return EvidenceQAResponse(**result.model_dump(), answer=result.answer)

    return router
