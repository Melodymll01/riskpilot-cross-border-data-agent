"""V2 案件证据索引 Worker。"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain.documents import Document, ProcessingJob
from domain.errors import InvalidDocumentContent, ProcessingJobNotFound
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
        if job.status != "running" or job.current_stage != "chunk":
            raise InvalidDocumentContent("只有处于 chunk 阶段的运行中任务可以建立索引")
        snapshot = self._repo.get_parse_snapshot(version.version_id)
        if snapshot is None:
            raise InvalidDocumentContent("DocumentVersion 缺少解析快照")
        bindings = self._repo.list_bindings_for_document(document.document_id)

        try:
            chunks = self._chunker.chunk(document, version, snapshot, bindings)
            chunked_at = max(self._clock(), job.updated_at, document.updated_at)
            indexing_job = job.advance(
                stage="index_vector",
                progress=0.75,
                at=chunked_at,
            )
            indexing_document = document.transition_to("indexing", at=chunked_at)
            self._repo.update_processing_state(indexing_document, indexing_job)

            embeddings = self._embedder.embed([chunk.text for chunk in chunks])
            completed_at = max(self._clock(), chunked_at)
            completed_job = indexing_job.complete(at=completed_at)
            ready_document = indexing_document.transition_to("ready", at=completed_at)
            self._index.complete_version_indexing(
                version.version_id,
                chunks,
                embeddings,
                ready_document,
                completed_job,
            )
            return IndexStageResult(
                document=ready_document,
                job=completed_job,
                chunks=chunks,
            )
        except Exception as exc:
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
                self._repo.update_processing_state(failed_document, failed_job)
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
