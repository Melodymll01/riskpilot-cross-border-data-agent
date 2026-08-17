"""文档 OCR 阶段 Worker。"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain.documents import Document, DocumentVersion, ProcessingJob
from domain.errors import (
    InvalidDocumentContent,
    ProcessingJobConflict,
    ProcessingJobNotFound,
)

if TYPE_CHECKING:
    from domain.document_content import DocumentParseSnapshot
    from domain.ports import DocumentOcrPort, DocumentRepoPort, ObjectStorePort


@dataclass(frozen=True)
class OcrStageResult:
    document: Document
    version: DocumentVersion
    job: ProcessingJob
    snapshot: DocumentParseSnapshot


class DocumentOcrWorker:
    def __init__(
        self,
        *,
        document_repo: DocumentRepoPort,
        object_store: ObjectStorePort,
        ocr: DocumentOcrPort,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._repo = document_repo
        self._objects = object_store
        self._ocr = ocr
        self._clock = clock

    def run(self, job_id: str) -> OcrStageResult:
        job = self._repo.get_job(job_id)
        if job is None:
            raise ProcessingJobNotFound(job_id)
        version = self._repo.get_version(job.document_version_id)
        if version is None:
            raise InvalidDocumentContent("处理任务关联的 DocumentVersion 不存在")
        document = self._repo.get(version.document_id)
        snapshot = self._repo.get_parse_snapshot(version.version_id)
        if document is None or snapshot is None:
            raise InvalidDocumentContent("OCR 缺少 Document 或解析快照")
        if job.status != "running" or job.current_stage != "ocr":
            raise InvalidDocumentContent("只有处于 OCR 阶段的运行中任务可以执行 OCR")
        try:
            content = self._objects.read(version.object_key)
            if hashlib.sha256(content).hexdigest() != version.sha256:
                raise InvalidDocumentContent("OCR 原始对象 SHA-256 与版本元数据不一致")
            updated_snapshot = self._ocr.apply_ocr(version, content, snapshot)
            finished_at = max(self._clock(), updated_snapshot.parsed_at, job.updated_at)
            chunking_job = job.advance(stage="chunk", progress=0.5, at=finished_at)
            chunking_document = document.transition_to("chunking", at=finished_at)
            updated_version = version.model_copy(
                update={
                    "parser_version": updated_snapshot.parser_version,
                    "page_count": updated_snapshot.page_count,
                }
            )
            self._repo.save_parse_result(
                updated_version,
                updated_snapshot,
                chunking_document,
                chunking_job,
                expected_job_revision=job.revision,
            )
            return OcrStageResult(
                document=chunking_document,
                version=updated_version,
                job=chunking_job,
                snapshot=updated_snapshot,
            )
        except ProcessingJobConflict:
            raise
        except Exception as exc:
            if not isinstance(
                exc,
                (FileNotFoundError, InvalidDocumentContent, KeyError),
            ):
                raise
            failed_at = max(self._clock(), job.updated_at, document.updated_at)
            latest_job = self._repo.get_job(job.job_id) or job
            latest_document = self._repo.get(document.document_id) or document
            if latest_job.status == "running":
                failed_job = latest_job.fail(
                    error_code=f"OCR_{type(exc).__name__.upper()}",
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
