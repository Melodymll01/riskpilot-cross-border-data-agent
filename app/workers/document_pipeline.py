"""数据库驱动、可重放的文档处理 Pipeline。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from domain.errors import InvalidDocumentContent, ProcessingJobNotFound

if TYPE_CHECKING:
    from app.workers.document_ocr import DocumentOcrWorker
    from app.workers.document_processing import DocumentProcessingWorker
    from app.workers.evidence_indexing import EvidenceIndexWorker
    from domain.ports import DocumentRepoPort

PipelineOutcome = Literal["completed", "cancelled", "failed", "in_progress"]


@dataclass(frozen=True)
class DocumentPipelineResult:
    job_id: str
    outcome: PipelineOutcome
    stage: str


class DocumentPipelineWorker:
    """每次从 Repository 重读状态，只通过 ID 驱动下一阶段。"""

    def __init__(
        self,
        *,
        document_repo: DocumentRepoPort,
        parser: DocumentProcessingWorker,
        ocr: DocumentOcrWorker,
        indexer: EvidenceIndexWorker,
        max_stage_steps: int = 8,
    ) -> None:
        if max_stage_steps < 1:
            raise ValueError("max_stage_steps 必须大于 0")
        self._repo = document_repo
        self._parser = parser
        self._ocr = ocr
        self._indexer = indexer
        self._max_stage_steps = max_stage_steps

    def run(self, job_id: str) -> DocumentPipelineResult:
        for _step in range(self._max_stage_steps):
            job = self._repo.get_job(job_id)
            if job is None:
                raise ProcessingJobNotFound(job_id)
            if job.status == "completed":
                return DocumentPipelineResult(job_id, "completed", job.current_stage)
            if job.status == "cancelled":
                return DocumentPipelineResult(job_id, "cancelled", job.current_stage)
            if job.status == "failed":
                return DocumentPipelineResult(job_id, "failed", job.current_stage)
            if job.status == "queued":
                self._parser.run_parse_stage(job_id)
                continue
            if job.status == "running" and job.current_stage == "extract_structure":
                self._parser.run_parse_stage(job_id)
                continue
            if job.status == "running" and job.current_stage == "ocr":
                self._ocr.run(job_id)
                continue
            if job.status == "running" and job.current_stage in {
                "chunk",
                "embedding",
                "index_vector",
            }:
                self._indexer.run(job_id)
                continue
            raise InvalidDocumentContent(
                f"处理任务 {job.job_id!r} 无法从 {job.status}/{job.current_stage} 恢复"
            )
        latest = self._repo.get_job(job_id)
        if latest is None:
            raise ProcessingJobNotFound(job_id)
        return DocumentPipelineResult(job_id, "in_progress", latest.current_stage)
