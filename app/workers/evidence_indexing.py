"""V2 案件证据索引 Worker。"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain.documents import Document, ProcessingJob
from domain.errors import (
    InvalidDocumentContent,
    ProcessingJobConflict,
    ProcessingJobNotFound,
)
from domain.evidence import EvidenceChunk

if TYPE_CHECKING:
    from domain.ports import (
        DocumentRepoPort,
        EmbedPort,
        EvidenceChunkerPort,
        EvidenceIndexPort,
    )


@dataclass(frozen=True)
class IndexStageResult:
    document: Document
    job: ProcessingJob
    chunks: list[EvidenceChunk]


class EvidenceIndexWorker:
    """从解析快照生成作用域证据块并完成索引。"""

    def __init__(
        self,
        *,
        document_repo: DocumentRepoPort,
        chunker: EvidenceChunkerPort,
        evidence_index: EvidenceIndexPort,
        embedder: EmbedPort,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._repo = document_repo
        self._chunker = chunker
        self._index = evidence_index
        self._embedder = embedder
        self._clock = clock

    def run(self, job_id: str) -> IndexStageResult:
        job, document = self._load(job_id)
        version = self._repo.get_version(job.document_version_id)
        if version is None:
            raise InvalidDocumentContent("处理任务关联的 DocumentVersion 不存在")
        existing_count = self._index.count_version(version.version_id)
        if existing_count > 0 and job.status == "completed" and document.status == "ready":
            return IndexStageResult(document=document, job=job, chunks=[])
        if job.status != "running" or job.current_stage not in {
            "chunk",
            "embedding",
            "index_vector",
        }:
            raise InvalidDocumentContent(
                "只有处于 chunk/embedding/index_vector 阶段的任务可以建立索引"
            )
        snapshot = self._repo.get_parse_snapshot(version.version_id)
        if snapshot is None:
            raise InvalidDocumentContent("DocumentVersion 缺少解析快照")
        bindings = self._repo.list_bindings_for_document(document.document_id)

        try:
            chunks = self._chunker.chunk(document, version, snapshot, bindings)
            embedding_job = job
            embedding_document = document
            if job.current_stage == "chunk":
                chunked_at = max(self._clock(), job.updated_at, document.updated_at)
                embedding_job = job.advance(
                    stage="embedding",
                    progress=0.65,
                    at=chunked_at,
                )
                embedding_document = document.transition_to("embedding", at=chunked_at)
                self._repo.update_processing_state(
                    embedding_document,
                    embedding_job,
                    expected_revision=job.revision,
                )

            embeddings = self._embedder.embed([chunk.text for chunk in chunks])
            _validate_embeddings(chunks, embeddings)
            indexing_job = embedding_job
            indexing_document = embedding_document
            if embedding_job.current_stage == "embedding":
                embedded_at = max(
                    self._clock(),
                    embedding_job.updated_at,
                    embedding_document.updated_at,
                )
                indexing_job = embedding_job.advance(
                    stage="index_vector",
                    progress=0.85,
                    at=embedded_at,
                )
                indexing_document = embedding_document.transition_to(
                    "indexing",
                    at=embedded_at,
                )
                self._repo.update_processing_state(
                    indexing_document,
                    indexing_job,
                    expected_revision=embedding_job.revision,
                )

            completed_at = max(
                self._clock(),
                indexing_job.updated_at,
                indexing_document.updated_at,
            )
            completed_job = indexing_job.complete(at=completed_at)
            ready_document = indexing_document.transition_to("ready", at=completed_at)
            self._index.complete_version_indexing(
                version.version_id,
                chunks,
                embeddings,
                ready_document,
                completed_job,
                expected_job_revision=indexing_job.revision,
            )
            return IndexStageResult(
                document=ready_document,
                job=completed_job,
                chunks=chunks,
            )
        except ProcessingJobConflict:
            raise
        except Exception as exc:
            if not isinstance(exc, InvalidDocumentContent):
                raise
            failed_at = max(self._clock(), job.updated_at, document.updated_at)
            latest_job = self._repo.get_job(job.job_id) or job
            latest_document = self._repo.get(document.document_id) or document
            if latest_job.status == "running":
                failed_job = latest_job.fail(
                    error_code=f"INDEX_{type(exc).__name__.upper()}",
                    error_message=str(exc),
                    at=max(failed_at, latest_job.updated_at),
                )
                failed_document = latest_document.transition_to(
                    "failed",
                    at=max(failed_at, latest_document.updated_at),
                )
                self._repo.update_processing_state(
                    failed_document,
                    failed_job,
                    expected_revision=latest_job.revision,
                )
            raise

    def _load(self, job_id: str) -> tuple[ProcessingJob, Document]:
        job = self._repo.get_job(job_id)
        if job is None:
            raise ProcessingJobNotFound(job_id)
        version = self._repo.get_version(job.document_version_id)
        if version is None:
            raise InvalidDocumentContent("处理任务关联的 DocumentVersion 不存在")
        document = self._repo.get(version.document_id)
        if document is None:
            raise InvalidDocumentContent("DocumentVersion 关联的 Document 不存在")
        return job, document


def _validate_embeddings(
    chunks: list[EvidenceChunk],
    embeddings: list[list[float]],
) -> None:
    if len(embeddings) != len(chunks):
        raise RuntimeError("Embedding 返回数量与 chunk 不一致")
    dimensions = {len(embedding) for embedding in embeddings}
    if not dimensions or 0 in dimensions or len(dimensions) != 1:
        raise RuntimeError("Embedding 维度为空或不一致")
    if any(not any(value != 0.0 for value in embedding) for embedding in embeddings):
        raise RuntimeError("Embedding 服务降级为零向量，拒绝写入索引")
