"""SqliteDocumentRepo 集成测试。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from domain import (
    Case,
    CaseDocument,
    Document,
    DocumentRepoPort,
    DocumentVersion,
    ProcessingJob,
    Workspace,
    WorkspaceMembership,
)
from infra.storage import SqliteCaseRepo, SqliteDocumentRepo, SqliteWorkspaceRepo
from infra.storage._db import SqliteConnectionPool

_SHA256 = "a" * 64


@pytest.fixture
def pool(tmp_path: Path) -> SqliteConnectionPool:
    return SqliteConnectionPool(str(tmp_path / "documents.db"))


@pytest.fixture
def workspace_repo(pool: SqliteConnectionPool) -> SqliteWorkspaceRepo:
    return SqliteWorkspaceRepo(pool)


@pytest.fixture
def case_repo(pool: SqliteConnectionPool) -> SqliteCaseRepo:
    return SqliteCaseRepo(pool)


@pytest.fixture
def document_repo(pool: SqliteConnectionPool) -> SqliteDocumentRepo:
    return SqliteDocumentRepo(pool)


def _seed_case(
    workspace_repo: SqliteWorkspaceRepo,
    case_repo: SqliteCaseRepo,
    *,
    workspace_id: str = "ws_001",
    case_id: str = "case_001",
) -> None:
    workspace = Workspace(
        workspace_id=workspace_id,
        name="跨境合规组",
        created_by="github:alice",
        created_at=100.0,
        updated_at=100.0,
    )
    membership = WorkspaceMembership(
        workspace_id=workspace_id,
        user_id="github:alice",
        role="admin",
        joined_at=100.0,
    )
    workspace_repo.create(workspace, membership)
    case_repo.create(
        Case(
            case_id=case_id,
            workspace_id=workspace_id,
            title="海外客服项目",
            owner_id="github:alice",
            created_at=100.0,
            updated_at=100.0,
        )
    )


def _upload_graph(
    *,
    document_id: str = "doc_001",
    version_id: str = "ver_001",
    job_id: str = "job_001",
    case_id: str = "case_001",
    status: str = "queued",
) -> tuple[Document, DocumentVersion, CaseDocument, ProcessingJob]:
    document = Document(
        document_id=document_id,
        workspace_id="ws_001",
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
    binding = CaseDocument(
        case_id=case_id,
        document_id=document_id,
        purpose="证明境外接收方责任",
        added_by="github:alice",
        added_at=100.0,
    )
    job = ProcessingJob(
        job_id=job_id,
        document_version_id=version_id,
        created_at=100.0,
        updated_at=100.0,
    )
    return document, version, binding, job


class TestDocumentRepoContract:
    def test_satisfies_port(self, document_repo: SqliteDocumentRepo) -> None:
        assert isinstance(document_repo, DocumentRepoPort)


class TestCreateUpload:
    def test_round_trip_all_objects(
        self,
        workspace_repo: SqliteWorkspaceRepo,
        case_repo: SqliteCaseRepo,
        document_repo: SqliteDocumentRepo,
    ) -> None:
        _seed_case(workspace_repo, case_repo)
        document, version, binding, job = _upload_graph()
        document_repo.create_upload(document, version, binding, job)

        assert document_repo.get(document.document_id) == document
        assert document_repo.get_version(version.version_id) == version
        assert document_repo.get_binding(binding.case_id, binding.document_id) == binding
        assert document_repo.get_job(job.job_id) == job
        assert document_repo.list_versions(document.document_id) == [version]
        assert document_repo.list_for_case(binding.case_id) == [document]

    def test_rejects_inconsistent_graph_before_writing(
        self,
        workspace_repo: SqliteWorkspaceRepo,
        case_repo: SqliteCaseRepo,
        document_repo: SqliteDocumentRepo,
    ) -> None:
        _seed_case(workspace_repo, case_repo)
        document, version, binding, job = _upload_graph()
        wrong_version = version.model_copy(update={"document_id": "doc_other"})
        with pytest.raises(ValueError, match="DocumentVersion"):
            document_repo.create_upload(document, wrong_version, binding, job)
        assert document_repo.get(document.document_id) is None

    def test_foreign_key_failure_rolls_back_all_rows(
        self,
        workspace_repo: SqliteWorkspaceRepo,
        case_repo: SqliteCaseRepo,
        document_repo: SqliteDocumentRepo,
    ) -> None:
        _seed_case(workspace_repo, case_repo)
        document, version, binding, job = _upload_graph(case_id="case_missing")
        with pytest.raises(sqlite3.IntegrityError):
            document_repo.create_upload(document, version, binding, job)

        assert document_repo.get(document.document_id) is None
        assert document_repo.get_version(version.version_id) is None
        assert document_repo.get_job(job.job_id) is None


class TestDocumentQueries:
    def test_list_for_case_filters_deleted(
        self,
        workspace_repo: SqliteWorkspaceRepo,
        case_repo: SqliteCaseRepo,
        document_repo: SqliteDocumentRepo,
    ) -> None:
        _seed_case(workspace_repo, case_repo)
        open_graph = _upload_graph()
        deleted_graph = _upload_graph(
            document_id="doc_deleted",
            version_id="ver_deleted",
            job_id="job_deleted",
            status="deleted",
        )
        document_repo.create_upload(*open_graph)
        document_repo.create_upload(*deleted_graph)

        assert [d.document_id for d in document_repo.list_for_case("case_001")] == ["doc_001"]
        assert {
            d.document_id for d in document_repo.list_for_case("case_001", include_deleted=True)
        } == {"doc_001", "doc_deleted"}

    def test_update_document_and_job(
        self,
        workspace_repo: SqliteWorkspaceRepo,
        case_repo: SqliteCaseRepo,
        document_repo: SqliteDocumentRepo,
    ) -> None:
        _seed_case(workspace_repo, case_repo)
        document, version, binding, job = _upload_graph()
        document_repo.create_upload(document, version, binding, job)

        updated_document = document.transition_to("parsing", at=101.0)
        document_repo.update_document(updated_document)
        updated_job = job.start(at=101.0).advance(
            stage="extract_text",
            progress=0.4,
            at=102.0,
        )
        document_repo.update_job(updated_job)

        assert document_repo.get(document.document_id) == updated_document
        assert document_repo.get_job(job.job_id) == updated_job
