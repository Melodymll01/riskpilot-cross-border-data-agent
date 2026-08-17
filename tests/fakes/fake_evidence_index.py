"""EvidenceIndexPort Fake。"""

from __future__ import annotations

from domain.documents import Document, ProcessingJob
from domain.evidence import EvidenceChunk, EvidenceSearchHit
from tests.fakes.fake_repos import InMemoryDocumentRepo


class FakeEvidenceIndex:
    def __init__(
        self,
        document_repo: InMemoryDocumentRepo | None = None,
    ) -> None:
        self.chunks: dict[str, tuple[EvidenceChunk, list[float]]] = {}
        self.search_calls: list[dict[str, object]] = []
        self.workspace_search_calls: list[dict[str, object]] = []
        self._document_repo = document_repo

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

    def complete_version_indexing(
        self,
        document_version_id: str,
        chunks: list[EvidenceChunk],
        embeddings: list[list[float]],
        document: Document,
        job: ProcessingJob,
        *,
        expected_job_revision: int,
    ) -> None:
        self.replace_version_chunks(document_version_id, chunks, embeddings)
        self.completed_document = document
        self.completed_job = job
        if self._document_repo is not None:
            self._document_repo.update_processing_state(
                document,
                job,
                expected_revision=expected_job_revision,
            )

    def search(
        self,
        *,
        workspace_id: str,
        case_id: str,
        query: str,
        query_embedding: list[float],
        top_k: int = 5,
    ) -> list[EvidenceSearchHit]:
        self.search_calls.append(
            {
                "workspace_id": workspace_id,
                "case_id": case_id,
                "query": query,
                "query_embedding": query_embedding,
                "top_k": top_k,
            }
        )
        scoped = [
            chunk
            for chunk, _ in self.chunks.values()
            if chunk.workspace_id == workspace_id and chunk.case_id == case_id
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

    def search_workspace(
        self,
        *,
        workspace_id: str,
        query: str,
        query_embedding: list[float],
        top_k: int = 5,
    ) -> list[EvidenceSearchHit]:
        self.workspace_search_calls.append(
            {
                "workspace_id": workspace_id,
                "query": query,
                "query_embedding": query_embedding,
                "top_k": top_k,
            }
        )
        if self._document_repo is None:
            return []
        deduplicated: dict[tuple[str, int, int], EvidenceChunk] = {}
        for chunk, _ in self.chunks.values():
            document = self._document_repo.get(chunk.document_id)
            if (
                chunk.workspace_id != workspace_id
                or document is None
                or document.document_type != "workspace_knowledge"
                or document.status != "ready"
                or document.current_version_id != chunk.document_version_id
            ):
                continue
            key = (
                chunk.document_version_id,
                chunk.page_number,
                chunk.chunk_index,
            )
            deduplicated.setdefault(key, chunk)
        return [
            EvidenceSearchHit(
                chunk=chunk,
                score=0.02,
                vector_score=0.5,
                bm25_score=1.0,
            )
            for chunk in list(deduplicated.values())[:top_k]
        ]

    def count_version(self, document_version_id: str) -> int:
        return sum(
            chunk.document_version_id == document_version_id for chunk, _ in self.chunks.values()
        )
