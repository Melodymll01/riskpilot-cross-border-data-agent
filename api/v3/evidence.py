"""V3 案件证据检索路由。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query

from api.v2.deps import make_require_owner
from api.v3.schemas import (
    EvidenceChunkOut,
    EvidenceSearchHitOut,
    EvidenceSearchResponse,
)

if TYPE_CHECKING:
    from app.container import AppContainer
    from domain.evidence import EvidenceSearchHit


def _to_hit_out(hit: EvidenceSearchHit) -> EvidenceSearchHitOut:
    chunk = hit.chunk
    return EvidenceSearchHitOut(
        chunk=EvidenceChunkOut(
            chunk_id=chunk.chunk_id,
            document_id=chunk.document_id,
            document_version_id=chunk.document_version_id,
            page_number=chunk.page_number,
            chunk_index=chunk.chunk_index,
            text=chunk.text,
            source_sha256=chunk.source_sha256,
        ),
        score=hit.score,
        vector_score=hit.vector_score,
        bm25_score=hit.bm25_score,
    )


def build_evidence_routes(container: AppContainer) -> APIRouter:
    router = APIRouter(prefix="/cases", tags=["v3-evidence"])
    require_owner = make_require_owner(container)

    @router.get(
        "/{case_id}/evidence/search",
        response_model=EvidenceSearchResponse,
        summary="在当前案件作用域内搜索证据",
    )
    def search_evidence(
        case_id: str,
        query: str = Query(min_length=1, max_length=2000),
        top_k: int = Query(default=5, ge=1, le=20),
        actor_id: str = Depends(require_owner),
    ) -> EvidenceSearchResponse:
        hits = container.evidence_search.search(
            actor_id,
            case_id=case_id,
            query=query,
            top_k=top_k,
        )
        return EvidenceSearchResponse(hits=[_to_hit_out(hit) for hit in hits])

    return router
