from __future__ import annotations

from domain.evidence import EvidenceChunk, EvidenceSearchHit


class FakeEvidenceIndex:
    def __init__(self) -> None:
        self.chunks: dict[str, tuple[EvidenceChunk, list[float]]] = {}
        self.search_calls: list[dict[str, object]] = []

    def replace_version_chunks(
        self,
        document_version_id: str,
        chunks: list[EvidenceChunk],
        embeddings: list[list[float]],
    ) -> None:
        self.chunks = {
            chunk_id: value
            for chunk_id, value in self.chunks.items()
            if value[0].document_version_id != document_version_id
        }
        self.chunks.update(
            {
                chunk.chunk_id: (chunk, embedding)
                for chunk, embedding in zip(chunks, embeddings, strict=True)
            }
        )

    def search(
        self,
        *,
        owner_id: str,
        query: str,
        query_embedding: list[float],
        top_k: int = 5,
        document_ids: list[str] | None = None,
        document_version_ids: list[str] | None = None,
    ) -> list[EvidenceSearchHit]:
        self.search_calls.append(
            {
                "owner_id": owner_id,
                "query": query,
                "query_embedding": query_embedding,
                "top_k": top_k,
                "document_ids": document_ids,
                "document_version_ids": document_version_ids,
            }
        )
        scoped = [
            chunk
            for chunk, _ in self.chunks.values()
            if chunk.owner_id == owner_id
            and (document_ids is None or chunk.document_id in document_ids)
            and (document_version_ids is None or chunk.document_version_id in document_version_ids)
        ]
        return [
            EvidenceSearchHit(
                chunk=chunk,
                score=0.02,
                vector_score=0.5,
                bm25_score=1.0,
            )
            for chunk in scoped[:top_k]
        ]

    def count_version(self, document_version_id: str) -> int:
        return sum(
            chunk.document_version_id == document_version_id for chunk, _ in self.chunks.values()
        )

    def delete_version(self, document_version_id: str) -> int:
        targets = [
            chunk_id
            for chunk_id, (chunk, _) in self.chunks.items()
            if chunk.document_version_id == document_version_id
        ]
        for chunk_id in targets:
            self.chunks.pop(chunk_id)
        return len(targets)
