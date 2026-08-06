"""V2 案件证据检索用例。"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from app.use_cases.case_management import CaseManagementUseCase
    from domain.evidence import EvidenceSearchHit
    from domain.ports import EmbedPort, EvidenceIndexPort


class EvidenceSearchUseCase:
    def __init__(
        self,
        *,
        evidence_index: EvidenceIndexPort,
        embedder: EmbedPort,
        case_management: CaseManagementUseCase,
    ) -> None:
        self._index = evidence_index
        self._embedder = embedder
        self._case_management = case_management

    def search(
        self,
        actor_id: str,
        *,
        case_id: str,
        query: str,
        top_k: int = 5,
    ) -> list[EvidenceSearchHit]:
        case = self._case_management.get_case(case_id, actor_id)
        query_embedding = self._embedder.embed([query])[0]
        return cast(
            "list[EvidenceSearchHit]",
            self._index.search(
                workspace_id=case.workspace_id,
                case_id=case.case_id,
                query=query,
                query_embedding=query_embedding,
                top_k=top_k,
            ),
        )
