"""SqliteDocumentRepo 集成测试。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from domain import Document, DocumentRepoPort, DocumentVersion, ProcessingJob, ProcessingJobConflict
from domain.document_content import DocumentParseSnapshot, ParsedPage
from infra.storage import SqliteDocumentRepo
from infra.storage._db import SqliteConnectionPool

_SHA256 = "a" * 64


@pytest.fixture
def pool(tmp_path: Path) -> SqliteConnectionPool:
    return SqliteConnectionPool(str(tmp_path / "documents.db"))


@pytest.fixture
def document_repo(pool: SqliteConnectionPool) -> SqliteDocumentRepo:
    return SqliteDocumentRepo(pool)


def _upload_graph(
    *,
    document_id: str = "doc_001",
    version_id: str = "ver_001",
    job_id: str = "job_001",
    status: str = "queued",
) -> tuple[Document, DocumentVersion, ProcessingJob]:
    document = Document(
        document_id=document_id,
        owner_id="ws_001",
        logical_name="数据出境合同.pdf",
        document_type="contract",
        status=status,  # type: ignore[arg-type]
        created_by="github:alice",
        current_version_id=version_id,
        created_at=100.0,
        updated_at=100.0,
    )
    version = DocumentVersion(
        version_id=version_id,
        document_id=document_id,
        version_number=1,
        object_key=f"ws_001/{document_id}/{version_id}/source.pdf",
        sha256=_SHA256,
        mime_type="application/pdf",
        size_bytes=1024,
        created_at=100.0,
    )
    job = ProcessingJob(
        job_id=job_id,
        document_version_id=version_id,
        created_at=100.0,
        updated_at=100.0,
    )
    return document, version, job


class TestDocumentRepoContract:
    def test_satisfies_port(self, document_repo: SqliteDocumentRepo) -> None:
        assert isinstance(document_repo, DocumentRepoPort)


class TestCreateUpload:
    def test_round_trip_all_objects(
        self,
        document_repo: SqliteDocumentRepo,
    ) -> None:
        document, version, job = _upload_graph()
        document_repo.create_upload(document, version, job)

        assert document_repo.get(document.document_id) == document
        assert document_repo.get_version(version.version_id) == version
        assert document_repo.get_job(job.job_id) == job
        assert document_repo.get_latest_job_for_version(version.version_id) == job
        assert document_repo.list_versions(document.document_id) == [version]

    def test_rejects_inconsistent_graph_before_writing(
        self,
        document_repo: SqliteDocumentRepo,
    ) -> None:
        document, version, job = _upload_graph()
        wrong_version = version.model_copy(update={"document_id": "doc_other"})
        with pytest.raises(ValueError, match="DocumentVersion"):
            document_repo.create_upload(document, wrong_version, job)
        assert document_repo.get(document.document_id) is None

    def test_job_conflict_rolls_back_new_document_and_version(
        self,
        document_repo: SqliteDocumentRepo,
    ) -> None:
        document_repo.create_upload(*_upload_graph())
        document, version, job = _upload_graph(document_id="new_doc", version_id="new_ver")
        with pytest.raises(sqlite3.IntegrityError):
            document_repo.create_upload(document, version, job)

        assert document_repo.get(document.document_id) is None
        assert document_repo.get_version(version.version_id) is None
        assert document_repo.get_job(job.job_id) is not None


class TestDocumentQueries:
    def test_list_for_owner_filters_deleted(
        self,
        document_repo: SqliteDocumentRepo,
    ) -> None:
        open_graph = _upload_graph()
        deleted_graph = _upload_graph(
            document_id="doc_deleted",
            version_id="ver_deleted",
            job_id="job_deleted",
            status="deleted",
        )
        document_repo.create_upload(*open_graph)
        document_repo.create_upload(*deleted_graph)

        assert [d.document_id for d in document_repo.list_for_owner("ws_001")] == ["doc_001"]
        assert {
            d.document_id for d in document_repo.list_for_owner("ws_001", include_deleted=True)
        } == {"doc_001", "doc_deleted"}
        assert {job.job_id for job in document_repo.list_jobs_for_owner("ws_001")} == {
            "job_001",
            "job_deleted",
        }

    def test_update_document_and_job(
        self,
        document_repo: SqliteDocumentRepo,
    ) -> None:
        document, version, job = _upload_graph()
        document_repo.create_upload(document, version, job)

        updated_document = document.transition_to("parsing", at=101.0)
        document_repo.update_document(updated_document)
        updated_job = job.start(at=101.0).advance(
            stage="extract_text",
            progress=0.4,
            at=102.0,
        )
        document_repo.update_job(updated_job, expected_revision=job.revision)

        assert document_repo.get(document.document_id) == updated_document
        assert document_repo.get_job(job.job_id) == updated_job

    def test_stale_job_revision_does_not_update_document_or_job(
        self,
        document_repo: SqliteDocumentRepo,
    ) -> None:
        document, version, job = _upload_graph()
        document_repo.create_upload(document, version, job)
        running = job.start(at=101.0)
        parsing = document.transition_to("parsing", at=101.0)
        document_repo.update_processing_state(
            parsing,
            running,
            expected_revision=job.revision,
        )

        stale_failed = job.start(at=102.0).fail(
            error_code="STALE",
            at=103.0,
        )
        stale_document = document.transition_to("failed", at=103.0)
        with pytest.raises(ProcessingJobConflict):
            document_repo.update_processing_state(
                stale_document,
                stale_failed,
                expected_revision=job.revision,
            )

        assert document_repo.get(document.document_id) == parsing
        assert document_repo.get_job(job.job_id) == running


class TestParseSnapshotPersistence:
    def test_save_parse_result_updates_all_state_atomically(
        self,
        document_repo: SqliteDocumentRepo,
    ) -> None:
        document, version, job = _upload_graph()
        document_repo.create_upload(document, version, job)
        running_job = job.start(stage="extract_structure", at=101.0).advance(
            stage="chunk",
            progress=0.5,
            at=102.0,
        )
        chunking_document = document.transition_to("parsing", at=101.0).transition_to(
            "chunking",
            at=102.0,
        )
        snapshot = DocumentParseSnapshot(
            snapshot_id="parse_001",
            document_version_id=version.version_id,
            parser_name="test-parser",
            parser_version="1.0.0",
            source_sha256=version.sha256,
            pages=[
                ParsedPage(
                    page_number=1,
                    text="正文",
                    extraction_method="native",
                )
            ],
            parsed_at=102.0,
        )
        parsed_version = version.model_copy(update={"parser_version": "1.0.0", "page_count": 1})
        document_repo.save_parse_result(
            parsed_version,
            snapshot,
            chunking_document,
            running_job,
            expected_job_revision=job.revision,
        )

        assert document_repo.get_version(version.version_id) == parsed_version
        assert document_repo.get_parse_snapshot(version.version_id) == snapshot
        assert document_repo.get(document.document_id) == chunking_document
        assert document_repo.get_job(job.job_id) == running_job
