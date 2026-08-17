"""V2 文档与处理任务领域模型测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from domain import (
    CaseDocument,
    Document,
    DocumentVersion,
    InvalidDocumentTransition,
    InvalidProcessingJobTransition,
    ProcessingJob,
)

_SHA256 = "a" * 64


def _document(**overrides: object) -> Document:
    values: dict[str, object] = {
        "document_id": "doc_001",
        "workspace_id": "ws_001",
        "logical_name": "数据出境合同.docx",
        "document_type": "contract",
        "created_by": "github:alice",
        "created_at": 100.0,
        "updated_at": 100.0,
    }
    values.update(overrides)
    return Document(**values)  # type: ignore[arg-type]


def _job(**overrides: object) -> ProcessingJob:
    values: dict[str, object] = {
        "job_id": "job_001",
        "document_version_id": "ver_001",
        "created_at": 100.0,
        "updated_at": 100.0,
    }
    values.update(overrides)
    return ProcessingJob(**values)  # type: ignore[arg-type]


class TestDocument:
    def test_defaults(self) -> None:
        document = _document()
        assert document.status == "uploaded"
        assert document.current_version_id is None

    def test_rejects_blank_logical_name(self) -> None:
        with pytest.raises(ValidationError, match="logical_name"):
            _document(logical_name="   ")

    def test_happy_path_to_ready(self) -> None:
        document = _document()
        path = ["queued", "parsing", "chunking", "embedding", "indexing", "ready"]
        for index, status in enumerate(path, start=1):
            document = document.transition_to(status, at=100.0 + index)  # type: ignore[arg-type]
        assert document.status == "ready"

    def test_ocr_path_to_ready(self) -> None:
        document = _document()
        path = ["queued", "parsing", "ocr", "chunking", "embedding", "indexing", "ready"]
        for index, status in enumerate(path, start=1):
            document = document.transition_to(status, at=100.0 + index)  # type: ignore[arg-type]
        assert document.status == "ready"

    def test_ready_can_requeue_for_reprocessing(self) -> None:
        document = _document(status="ready")
        assert document.transition_to("queued", at=101.0).status == "queued"

    def test_deleted_is_terminal(self) -> None:
        document = _document().transition_to("deleted", at=101.0)
        with pytest.raises(InvalidDocumentTransition):
            document.transition_to("queued", at=102.0)

    def test_invalid_transition_rejected(self) -> None:
        with pytest.raises(InvalidDocumentTransition):
            _document().transition_to("ready", at=101.0)


class TestDocumentVersion:
    def test_happy_path(self) -> None:
        version = DocumentVersion(
            version_id="ver_001",
            document_id="doc_001",
            version_number=1,
            object_key="ws_001/doc_001/ver_001/source.docx",
            sha256=_SHA256,
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            size_bytes=1024,
            created_at=100.0,
        )
        assert version.page_count is None
        assert version.version_number == 1

    @pytest.mark.parametrize("sha256", ["A" * 64, "a" * 63, "g" * 64])
    def test_invalid_sha256_rejected(self, sha256: str) -> None:
        with pytest.raises(ValidationError, match="sha256"):
            DocumentVersion(
                version_id="ver_001",
                document_id="doc_001",
                version_number=1,
                object_key="objects/source.pdf",
                sha256=sha256,
                mime_type="application/pdf",
                size_bytes=1,
                created_at=100.0,
            )

    def test_empty_file_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DocumentVersion(
                version_id="ver_001",
                document_id="doc_001",
                version_number=1,
                object_key="objects/source.pdf",
                sha256=_SHA256,
                mime_type="application/pdf",
                size_bytes=0,
                created_at=100.0,
            )


class TestCaseDocument:
    def test_happy_path(self) -> None:
        binding = CaseDocument(
            case_id="case_001",
            document_id="doc_001",
            purpose="证明境外接收方责任",
            added_by="github:alice",
            added_at=100.0,
        )
        assert binding.purpose == "证明境外接收方责任"


class TestProcessingJob:
    def test_defaults(self) -> None:
        job = _job()
        assert job.status == "queued"
        assert job.current_stage == "validate"
        assert job.progress == 0.0

    def test_start_advance_complete(self) -> None:
        job = _job().start(at=101.0)
        assert job.status == "running"
        assert job.started_at == 101.0
        job = job.advance(stage="extract_text", progress=0.3, at=102.0)
        job = job.advance(stage="index_vector", progress=0.8, at=103.0)
        job = job.complete(at=104.0)
        assert job.status == "completed"
        assert job.current_stage == "ready"
        assert job.progress == 1.0
        assert job.completed_at == 104.0

    def test_progress_cannot_move_backwards(self) -> None:
        job = (
            _job()
            .start(at=101.0)
            .advance(
                stage="extract_text",
                progress=0.5,
                at=102.0,
            )
        )
        with pytest.raises(ValueError, match="进度"):
            job.advance(stage="chunk", progress=0.4, at=103.0)

    def test_failed_job_can_retry(self) -> None:
        failed = (
            _job()
            .start(at=101.0)
            .fail(
                error_code="PARSER_FAILED",
                error_message="文档损坏",
                at=102.0,
            )
        )
        assert failed.status == "failed"
        retried = failed.retry(at=103.0)
        assert retried.status == "queued"
        assert retried.retry_count == 1
        assert retried.progress == 0.0
        assert retried.error_code is None

    def test_queued_job_cannot_complete(self) -> None:
        with pytest.raises(InvalidProcessingJobTransition):
            _job().complete(at=101.0)

    def test_running_job_cannot_retry_without_failure(self) -> None:
        running = _job().start(at=101.0)
        with pytest.raises(InvalidProcessingJobTransition):
            running.retry(at=102.0)

    def test_queued_job_can_cancel(self) -> None:
        cancelled = _job().cancel(at=101.0)
        assert cancelled.status == "cancelled"
        assert cancelled.completed_at == 101.0

    def test_completed_job_is_terminal(self) -> None:
        completed = _job().start(at=101.0).complete(at=102.0)
        with pytest.raises(InvalidProcessingJobTransition):
            completed.retry(at=103.0)

    def test_failed_status_requires_error_code(self) -> None:
        with pytest.raises(ValidationError, match="error_code"):
            _job(
                status="failed",
                started_at=101.0,
                completed_at=102.0,
            )
