"""V2 案件证据检索用例。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from domain.evidence import EvidenceSearchHit
    from domain.ports import EmbedPort, EvidenceIndexPort, RunControlPort


class EvidenceSearchUseCase:
    def __init__(
        self,
        *,
        evidence_index: EvidenceIndexPort,
        embedder: EmbedPort,
    ) -> None:
        self._index = evidence_index
        self._embedder = embedder

    def search(
        self,
        actor_id: str,
        *,
        document_ids: list[str] | None = None,
        document_version_ids: list[str] | None = None,
        query: str,
        top_k: int = 5,
        runtime_control: RunControlPort | None = None,
    ) -> list[EvidenceSearchHit]:
        query_embedding = self._embedder.embed([query], runtime_control=runtime_control)[0]
        if runtime_control is not None:
            runtime_control.check(stage="tool")
        result = self._index.search(
            owner_id=actor_id,
            document_ids=document_ids,
            document_version_ids=document_version_ids,
            query=query,
            query_embedding=query_embedding,
            top_k=top_k,
        )
        if runtime_control is not None:
            runtime_control.check(stage="tool")
        return result
