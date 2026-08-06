"""V3 案件事实资源路由。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query, status

from api.v2.deps import make_require_owner
from api.v3.schemas import (
    CaseFactOut,
    CreateFactRequest,
    FactDetailResponse,
    FactEvidenceOut,
    FactListResponse,
    ReviseFactRequest,
    TransitionFactRequest,
)

if TYPE_CHECKING:
    from app.container import AppContainer
    from app.use_cases import FactDetail, FactEvidenceInput
    from domain.facts import CaseFact, CaseFactEvidence


def _to_fact_out(fact: CaseFact) -> CaseFactOut:
    return CaseFactOut(
        fact_id=fact.fact_id,
        case_id=fact.case_id,
        field_name=fact.field_name,
        value=fact.value,
        status=fact.status,
        source_type=fact.source_type,
        confidence=fact.confidence,
        criticality=fact.criticality,
        version=fact.version,
        created_by=fact.created_by,
        confirmed_by=fact.confirmed_by,
        confirmed_at=fact.confirmed_at,
        created_at=fact.created_at,
        updated_at=fact.updated_at,
    )


def _to_evidence_out(evidence: CaseFactEvidence) -> FactEvidenceOut:
    return FactEvidenceOut(
        evidence_id=evidence.evidence_id,
        fact_version=evidence.fact_version,
        document_id=evidence.document_id,
        document_version_id=evidence.document_version_id,
        page_number=evidence.page_number,
        quote=evidence.quote,
        start_offset=evidence.start_offset,
        end_offset=evidence.end_offset,
        confidence=evidence.confidence,
        created_at=evidence.created_at,
    )


def _to_detail_out(detail: FactDetail) -> FactDetailResponse:
    return FactDetailResponse(
        fact=_to_fact_out(detail.fact),
        evidence=[_to_evidence_out(item) for item in detail.evidence],
    )


def _evidence_inputs(body: CreateFactRequest | ReviseFactRequest) -> list[FactEvidenceInput]:
    from app.use_cases import FactEvidenceInput

    return [FactEvidenceInput(**item.model_dump()) for item in body.evidence]


def build_fact_routes(container: AppContainer) -> APIRouter:
    router = APIRouter(tags=["v3-facts"])
    require_owner = make_require_owner(container)

    @router.post(
        "/cases/{case_id}/facts",
        response_model=FactDetailResponse,
        status_code=status.HTTP_201_CREATED,
        summary="创建案件事实候选",
    )
    def create_fact(
        case_id: str,
        body: CreateFactRequest,
        actor_id: str = Depends(require_owner),
    ) -> FactDetailResponse:
        detail = container.fact_management.create_fact(
            actor_id,
            case_id=case_id,
            field_name=body.field_name,
            value=body.value,
            source_type=body.source_type,
            confidence=body.confidence,
            criticality=body.criticality,
            evidence=_evidence_inputs(body),
        )
        return _to_detail_out(detail)

    @router.get(
        "/cases/{case_id}/facts",
        response_model=FactListResponse,
        summary="列出案件事实",
    )
    def list_facts(
        case_id: str,
        statuses: list[str] | None = Query(default=None),
        actor_id: str = Depends(require_owner),
    ) -> FactListResponse:
        facts = container.fact_management.list_facts(
            case_id,
            actor_id,
            statuses=set(statuses) if statuses else None,
        )
        return FactListResponse(facts=[_to_fact_out(fact) for fact in facts])

    @router.get(
        "/facts/{fact_id}",
        response_model=FactDetailResponse,
        summary="获取当前事实版本及证据",
    )
    def get_fact(
        fact_id: str,
        actor_id: str = Depends(require_owner),
    ) -> FactDetailResponse:
        return _to_detail_out(container.fact_management.get_detail(fact_id, actor_id))

    @router.post(
        "/facts/{fact_id}/revisions",
        response_model=FactDetailResponse,
        summary="创建事实新版本",
    )
    def revise_fact(
        fact_id: str,
        body: ReviseFactRequest,
        actor_id: str = Depends(require_owner),
    ) -> FactDetailResponse:
        detail = container.fact_management.revise_fact(
            fact_id,
            actor_id,
            value=body.value,
            source_type=body.source_type,
            confidence=body.confidence,
            evidence=_evidence_inputs(body),
        )
        return _to_detail_out(detail)

    @router.post(
        "/facts/{fact_id}/transitions",
        response_model=CaseFactOut,
        summary="确认、拒绝或标记冲突事实",
    )
    def transition_fact(
        fact_id: str,
        body: TransitionFactRequest,
        actor_id: str = Depends(require_owner),
    ) -> CaseFactOut:
        fact = container.fact_management.transition_fact(
            fact_id,
            actor_id,
            body.target,
        )
        return _to_fact_out(fact)

    return router
