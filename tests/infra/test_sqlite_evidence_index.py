"""SqliteEvidenceIndex 作用域和混合检索测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from domain import (
    Case,
    CaseDocument,
    Document,
    DocumentVersion,
    EvidenceChunk,
    EvidenceIndexPort,
    ProcessingJob,
    Workspace,
    WorkspaceMembership,
)
from infra.evidence import SqliteEvidenceIndex
from infra.storage import SqliteCaseRepo, SqliteDocumentRepo, SqliteWorkspaceRepo
from infra.storage._db import SqliteConnectionPool


@pytest.fixture
def pool(tmp_path: Path) -> SqliteConnectionPool:
    return SqliteConnectionPool(str(tmp_path / "evidence.db"))


@pytest.fixture
def index(pool: SqliteConnectionPool) -> SqliteEvidenceIndex:
    return SqliteEvidenceIndex(pool)


def _chunk(
    chunk_id: str,
    *,
    workspace_id: str,
    case_id: str,
    text: str,
) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        workspace_id=workspace_id,
        case_id=case_id,
        document_id=f"doc_{chunk_id}",
        document_version_id=f"ver_{chunk_id}",
        page_number=1,
        chunk_index=0,
        text=text,
        source_sha256="a" * 64,
        created_at=100.0,
    )


def _seed_scope(pool: SqliteConnectionPool, chunk: EvidenceChunk) -> None:
    workspace_repo = SqliteWorkspaceRepo(pool)
    case_repo = SqliteCaseRepo(pool)
    document_repo = SqliteDocumentRepo(pool)
    if workspace_repo.get(chunk.workspace_id) is None:
        workspace_repo.create(
            Workspace(
                workspace_id=chunk.workspace_id,
                name=chunk.workspace_id,
                created_by="github:alice",
                created_at=100.0,
                updated_at=100.0,
            ),
            WorkspaceMembership(
                workspace_id=chunk.workspace_id,
                user_id="github:alice",
                role="admin",
                joined_at=100.0,
            ),
        )
    if case_repo.get(chunk.case_id) is None:
        case_repo.create(
            Case(
                case_id=chunk.case_id,
                workspace_id=chunk.workspace_id,
                title=chunk.case_id,
                owner_id="github:alice",
                created_at=100.0,
                updated_at=100.0,
            )
        )
    version = DocumentVersion(
        version_id=chunk.document_version_id,
        document_id=chunk.document_id,
        version_number=1,
        object_key=f"{chunk.workspace_id}/{chunk.document_id}/source.txt",
        sha256=chunk.source_sha256,
        mime_type="text/plain",
        size_bytes=10,
        created_at=100.0,
    )
    document_repo.create_upload(
        Document(
            document_id=chunk.document_id,
            workspace_id=chunk.workspace_id,
            logical_name=f"{chunk.document_id}.txt",
            document_type="case_material",
            status="chunking",
            created_by="github:alice",
            current_version_id=version.version_id,
            created_at=100.0,
            updated_at=100.0,
        ),
        version,
        CaseDocument(
            case_id=chunk.case_id,
            document_id=chunk.document_id,
            added_by="github:alice",
            added_at=100.0,
        ),
        ProcessingJob(
            job_id=f"job_{chunk.chunk_id}",
            document_version_id=version.version_id,
            created_at=100.0,
            updated_at=100.0,
        ),
    )


class TestEvidenceIndex:
    def test_satisfies_port(self, index: SqliteEvidenceIndex) -> None:
        assert isinstance(index, EvidenceIndexPort)

    def test_sql_scope_filter_prevents_cross_case_leak(
        self,
        pool: SqliteConnectionPool,
        index: SqliteEvidenceIndex,
    ) -> None:
        case_a = _chunk(
            "a",
            workspace_id="ws_001",
            case_id="case_a",
            text="普通说明",
        )
        case_b = _chunk(
            "b",
            workspace_id="ws_001",
            case_id="case_b",
            text="境外接收方责任高度匹配",
        )
        other_workspace = _chunk(
            "c",
            workspace_id="ws_002",
            case_id="case_other",
            text="境外接收方责任更高匹配",
        )
        for chunk, embedding in (
            (case_a, [1.0, 0.0]),
            (case_b, [1.0, 0.0]),
            (other_workspace, [1.0, 0.0]),
        ):
            _seed_scope(pool, chunk)
            index.replace_version_chunks(
                chunk.document_version_id,
                [chunk],
                [embedding],
            )

        hits = index.search(
            workspace_id="ws_001",
            case_id="case_a",
            query="境外接收方责任",
            query_embedding=[1.0, 0.0],
            top_k=5,
        )
        assert [hit.chunk.chunk_id for hit in hits] == ["a"]

    def test_hybrid_ranking_uses_bm25_signal(
        self,
        pool: SqliteConnectionPool,
        index: SqliteEvidenceIndex,
    ) -> None:
        keyword_hit = _chunk(
            "keyword",
            workspace_id="ws_001",
            case_id="case_001",
            text="境外接收方必须承担安全保护责任",
        )
        vector_only = _chunk(
            "vector",
            workspace_id="ws_001",
            case_id="case_001",
            text="无关文本",
        )
        _seed_scope(pool, keyword_hit)
        _seed_scope(pool, vector_only)
        index.replace_version_chunks(
            keyword_hit.document_version_id,
            [keyword_hit],
            [[0.8, 0.2]],
        )
        index.replace_version_chunks(
            vector_only.document_version_id,
            [vector_only],
            [[1.0, 0.0]],
        )
        hits = index.search(
            workspace_id="ws_001",
            case_id="case_001",
            query="境外接收方责任",
            query_embedding=[1.0, 0.0],
            top_k=2,
        )
        assert hits[0].chunk.chunk_id == "keyword"
        assert hits[0].bm25_score > 0
        assert hits[1].vector_score >= hits[0].vector_score

    def test_replace_version_is_idempotent(
        self,
        pool: SqliteConnectionPool,
        index: SqliteEvidenceIndex,
    ) -> None:
        old = _chunk(
            "old",
            workspace_id="ws_001",
            case_id="case_001",
            text="old",
        )
        new = old.model_copy(update={"chunk_id": "new", "text": "new"})
        _seed_scope(pool, old)
        index.replace_version_chunks(old.document_version_id, [old], [[1.0]])
        index.replace_version_chunks(old.document_version_id, [new], [[1.0]])
        assert index.count_version(old.document_version_id) == 1
        hits = index.search(
            workspace_id="ws_001",
            case_id="case_001",
            query="new",
            query_embedding=[1.0],
        )
        assert [hit.chunk.chunk_id for hit in hits] == ["new"]

    def test_rejects_chunk_without_case_document_binding(
        self,
        pool: SqliteConnectionPool,
        index: SqliteEvidenceIndex,
    ) -> None:
        chunk = _chunk(
            "orphan",
            workspace_id="ws_001",
            case_id="case_001",
            text="orphan",
        )
        _seed_scope(pool, chunk)
        pool.get().execute(
            "DELETE FROM case_documents WHERE case_id = ? AND document_id = ?",
            (chunk.case_id, chunk.document_id),
        )
        pool.get().commit()
        with pytest.raises(ValueError, match="不存在"):
            index.replace_version_chunks(
                chunk.document_version_id,
                [chunk],
                [[1.0]],
            )

    def test_complete_indexing_updates_chunks_document_and_job_atomically(
        self,
        pool: SqliteConnectionPool,
        index: SqliteEvidenceIndex,
    ) -> None:
        chunk = _chunk(
            "complete",
            workspace_id="ws_001",
            case_id="case_001",
            text="境外接收方责任",
        )
        _seed_scope(pool, chunk)
        document_repo = SqliteDocumentRepo(pool)
        document = document_repo.get(chunk.document_id)
        job = document_repo.get_job(f"job_{chunk.chunk_id}")
        assert document is not None
        assert job is not None

        running_job = job.start(stage="extract_structure", at=101.0).advance(
            stage="chunk",
            progress=0.5,
            at=102.0,
        )
        document_repo.update_job(running_job)
        ready_document = document.transition_to("indexing", at=101.0).transition_to(
            "ready",
            at=103.0,
        )
        completed_job = running_job.advance(
            stage="index_vector",
            progress=0.75,
            at=103.0,
        ).complete(at=104.0)

        index.complete_version_indexing(
            chunk.document_version_id,
            [chunk],
            [[1.0, 0.0]],
            ready_document,
            completed_job,
        )

        assert index.count_version(chunk.document_version_id) == 1
        assert document_repo.get(chunk.document_id) == ready_document
        assert document_repo.get_job(job.job_id) == completed_job
