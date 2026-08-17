"""DocumentProcessingWorker 状态流转测试。"""

from __future__ import annotations

import hashlib

import pytest

from app.workers import DocumentProcessingWorker
from domain import (
    CaseDocument,
    Document,
    DocumentVersion,
    InvalidDocumentContent,
    ProcessingJob,
)
from domain.document_content import DocumentParseSnapshot, ParsedPage
from tests.fakes import FakeDocumentParser, FakeObjectStore, InMemoryDocumentRepo


def _seed(
    *,
    content: bytes = b"text",
    parser: FakeDocumentParser | None = None,
) -> tuple[
    DocumentProcessingWorker,
    InMemoryDocumentRepo,
    FakeObjectStore,
    ProcessingJob,
]:
    repo = InMemoryDocumentRepo()
    objects = FakeObjectStore()
    version = DocumentVersion(
        version_id="ver_001",
        document_id="doc_001",
        version_number=1,
        object_key="ws/doc/ver/source.txt",
        sha256=hashlib.sha256(content).hexdigest(),
        mime_type="text/plain",
        size_bytes=len(content),
        created_at=100.0,
    )
    document = Document(
        document_id="doc_001",
        workspace_id="ws_001",
        logical_name="source.txt",
        document_type="case_material",
        status="queued",
        created_by="github:alice",
        current_version_id=version.version_id,
        created_at=100.0,
        updated_at=100.0,
    )
    binding = CaseDocument(
        case_id="case_001",
        document_id=document.document_id,
        added_by="github:alice",
        added_at=100.0,
    )
    job = ProcessingJob(
        job_id="job_001",
        document_version_id=version.version_id,
        current_stage="extract_structure",
        created_at=100.0,
        updated_at=100.0,
    )
    repo.create_upload(document, version, binding, job)
    objects.put(version.object_key, content)
    times = iter([101.0, 102.0, 103.0, 104.0])
    worker = DocumentProcessingWorker(
        document_repo=repo,
        object_store=objects,
        parser=parser or FakeDocumentParser(),
        clock=lambda: next(times),
    )
    return worker, repo, objects, job


class TestParseStage:
    def test_success_advances_to_chunk_without_completing(self) -> None:
        worker, repo, _, job = _seed()
        result = worker.run_parse_stage(job.job_id)
        assert result.document.status == "chunking"
        assert result.job.status == "running"
        assert result.job.current_stage == "chunk"
        assert result.job.progress == 0.5
        assert result.version.parser_version == "test"
        assert result.version.page_count == 1
        assert repo.get_parse_snapshot("ver_001") == result.snapshot

    def test_blank_page_routes_to_ocr(self) -> None:
        class OcrParser(FakeDocumentParser):
            def parse(
                self,
                version: DocumentVersion,
                content: bytes,
            ) -> DocumentParseSnapshot:
                return DocumentParseSnapshot(
                    snapshot_id="parse_ocr",
                    document_version_id=version.version_id,
                    parser_name="fake",
                    parser_version="test",
                    source_sha256=version.sha256,
                    pages=[
                        ParsedPage(
                            page_number=1,
                            extraction_method="empty",
                        )
                    ],
                    parsed_at=101.0,
                )

        worker, _, _, job = _seed(parser=OcrParser())
        result = worker.run_parse_stage(job.job_id)
        assert result.document.status == "ocr"
        assert result.job.current_stage == "ocr"
        assert result.job.progress == 0.35

    def test_replay_after_snapshot_is_idempotent(self) -> None:
        parser = FakeDocumentParser()
        worker, _, _, job = _seed(parser=parser)
        first = worker.run_parse_stage(job.job_id)
        second = worker.run_parse_stage(job.job_id)
        assert second == first
        assert len(parser.calls) == 1

    def test_replay_after_crash_in_running_parse_stage_resumes(self) -> None:
        parser = FakeDocumentParser()
        worker, repo, _, job = _seed(parser=parser)
        document = repo.get("doc_001")
        assert document is not None
        running = job.start(stage="extract_structure", at=101.0)
        parsing = document.transition_to("parsing", at=101.0)
        repo.update_processing_state(
            parsing,
            running,
            expected_revision=job.revision,
        )

        result = worker.run_parse_stage(job.job_id)

        assert result.job.current_stage == "chunk"
        assert result.document.status == "chunking"
        assert len(parser.calls) == 1


class TestParseFailures:
    def test_parser_failure_marks_job_and_document_failed(self) -> None:
        parser = FakeDocumentParser(
            raise_error=InvalidDocumentContent("broken"),
        )
        worker, repo, _, job = _seed(parser=parser)
        with pytest.raises(InvalidDocumentContent, match="broken"):
            worker.run_parse_stage(job.job_id)
        failed_job = repo.get_job(job.job_id)
        failed_document = repo.get("doc_001")
        assert failed_job is not None
        assert failed_job.status == "failed"
        assert failed_job.error_code == "PARSE_INVALID_CONTENT"
        assert failed_document is not None
        assert failed_document.status == "failed"

    def test_missing_object_marks_failed(self) -> None:
        worker, repo, objects, job = _seed()
        objects.delete("ws/doc/ver/source.txt")
        with pytest.raises(KeyError):
            worker.run_parse_stage(job.job_id)
        failed_job = repo.get_job(job.job_id)
        assert failed_job is not None
        assert failed_job.status == "failed"

    def test_hash_mismatch_marks_failed(self) -> None:
        worker, repo, objects, job = _seed()
        objects.objects["ws/doc/ver/source.txt"] = b"tampered"
        with pytest.raises(InvalidDocumentContent, match="SHA-256"):
            worker.run_parse_stage(job.job_id)
        failed_job = repo.get_job(job.job_id)
        assert failed_job is not None
        assert failed_job.status == "failed"

    def test_transient_parser_failure_preserves_running_stage_for_retry(self) -> None:
        parser = FakeDocumentParser(
            raise_error=ConnectionError("parser service unavailable"),
        )
        worker, repo, _, job = _seed(parser=parser)

        with pytest.raises(ConnectionError, match="unavailable"):
            worker.run_parse_stage(job.job_id)

        current_job = repo.get_job(job.job_id)
        current_document = repo.get("doc_001")
        assert current_job is not None and current_document is not None
        assert current_job.status == "running"
        assert current_job.current_stage == "extract_structure"
        assert current_document.status == "parsing"
