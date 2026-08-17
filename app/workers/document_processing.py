"""V2 文档解析阶段 Worker。"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain.document_content import DocumentParseSnapshot
from domain.documents import (
    Document,
    DocumentStatus,
    DocumentVersion,
    ProcessingJob,
    ProcessingStage,
)
from domain.errors import (
    InvalidDocumentContent,
    ProcessingJobConflict,
    ProcessingJobNotFound,
)

if TYPE_CHECKING:
    from domain.ports import DocumentParserPort, DocumentRepoPort, ObjectStorePort


@dataclass(frozen=True)
class ParseStageResult:
    document: Document
    version: DocumentVersion
    job: ProcessingJob
    snapshot: DocumentParseSnapshot
    next_stage: ProcessingStage


class DocumentProcessingWorker:
    """执行解析阶段，并把任务推进到 OCR 或 chunk 阶段。"""

    def __init__(
        self,
        *,
        document_repo: DocumentRepoPort,
        object_store: ObjectStorePort,
        parser: DocumentParserPort,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._repo = document_repo
        self._objects = object_store
        self._parser = parser
        self._clock = clock

    def run_parse_stage(self, job_id: str) -> ParseStageResult:
        job, version, document = self._load_graph(job_id)
        existing_snapshot = self._repo.get_parse_snapshot(version.version_id)
        if (
            existing_snapshot is not None
            and job.status == "running"
            and job.current_stage in {"ocr", "chunk"}
        ):
            return ParseStageResult(
                document=document,
                version=version,
                job=job,
                snapshot=existing_snapshot,
                next_stage=job.current_stage,
            )
        if job.status == "running" and job.current_stage == "extract_structure":
            running_job = job
            parsing_document = document
            if document.status != "parsing":
                raise InvalidDocumentContent(
                    "running/extract_structure 任务的 Document 必须处于 parsing"
                )
            started_at = job.started_at or job.updated_at
        elif job.status == "queued":
            started_at = self._clock()
            running_job = job.start(stage="extract_structure", at=started_at)
            parsing_document = document.transition_to("parsing", at=started_at)
            self._repo.update_processing_state(
                parsing_document,
                running_job,
                expected_revision=job.revision,
            )
        else:
            raise InvalidDocumentContent(
                f"处理任务 {job.job_id!r} 当前状态 {job.status!r} 不能执行解析阶段"
            )

        try:
            content = self._objects.read(version.object_key)
            actual_sha256 = hashlib.sha256(content).hexdigest()
            if actual_sha256 != version.sha256:
                raise InvalidDocumentContent("原始对象 SHA-256 与版本元数据不一致")
            snapshot = self._parser.parse(version, content)
            requires_ocr = any(page.extraction_method == "empty" for page in snapshot.pages)
            finished_at = max(self._clock(), snapshot.parsed_at, started_at)
            next_document_status: DocumentStatus = "ocr" if requires_ocr else "chunking"
            next_stage: ProcessingStage = "ocr" if requires_ocr else "chunk"
            next_progress = 0.35 if requires_ocr else 0.5
            updated_document = parsing_document.transition_to(
                next_document_status,
                at=finished_at,
            )
            updated_job = running_job.advance(
                stage=next_stage,
                progress=next_progress,
                at=finished_at,
            )
            updated_version = version.model_copy(
                update={
                    "parser_version": snapshot.parser_version,
                    "page_count": snapshot.page_count,
                }
            )
            self._repo.save_parse_result(
                updated_version,
                snapshot,
                updated_document,
                updated_job,
                expected_job_revision=running_job.revision,
            )
            return ParseStageResult(
                document=updated_document,
                version=updated_version,
                job=updated_job,
                snapshot=snapshot,
                next_stage=next_stage,
            )
        except ProcessingJobConflict:
            raise
        except Exception as exc:
            if not _is_permanent_parse_error(exc):
                raise
            failed_at = max(self._clock(), started_at)
            failed_job = running_job.fail(
                error_code=_error_code(exc),
                error_message=str(exc),
                at=failed_at,
            )
            failed_document = parsing_document.transition_to("failed", at=failed_at)
            self._repo.update_processing_state(
                failed_document,
                failed_job,
                expected_revision=running_job.revision,
            )
            raise

    def _load_graph(
        self,
        job_id: str,
    ) -> tuple[ProcessingJob, DocumentVersion, Document]:
        job = self._repo.get_job(job_id)
        if job is None:
            raise ProcessingJobNotFound(job_id)
        version = self._repo.get_version(job.document_version_id)
        if version is None:
            raise InvalidDocumentContent("处理任务关联的 DocumentVersion 不存在")
        document = self._repo.get(version.document_id)
        if document is None:
            raise InvalidDocumentContent("DocumentVersion 关联的 Document 不存在")
        return job, version, document


def _error_code(exc: Exception) -> str:
    if isinstance(exc, FileNotFoundError):
        return "OBJECT_NOT_FOUND"
    if isinstance(exc, InvalidDocumentContent):
        return "PARSE_INVALID_CONTENT"
    return f"PARSE_{type(exc).__name__.upper()}"


def _is_permanent_parse_error(exc: Exception) -> bool:
    return isinstance(exc, (FileNotFoundError, InvalidDocumentContent, KeyError))
