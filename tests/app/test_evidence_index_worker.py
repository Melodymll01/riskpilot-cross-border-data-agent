"""EvidenceIndexWorker 测试。"""

from __future__ import annotations

import hashlib

import pytest

from app.workers import EvidenceIndexWorker
from domain import (
    CaseDocument,
    Document,
    DocumentParseSnapshot,
    DocumentVersion,
    ParsedPage,
    ProcessingJob,
)
from tests.fakes import (
    FakeEmbed,
    FakeEvidenceChunker,
    FakeEvidenceIndex,
    InMemoryDocumentRepo,
)


def _setup():
    repo = InMemoryDocumentRepo()
    index = FakeEvidenceIndex(repo)
    version = DocumentVersion(
        version_id="ver_001",
        document_id="doc_001",
        version_number=1,
        object_key="objects/source.txt",
        sha256=hashlib.sha256(b"text").hexdigest(),
        mime_type="text/plain",
        size_bytes=4,
        parser_version="test",
        page_count=1,
        created_at=100.0,
    )
    document = Document(
        document_id="doc_001",
        workspace_id="ws_001",
        logical_name="source.txt",
        document_type="case_material",
        status="chunking",
        created_by="github:alice",
        current_version_id=version.version_id,
        created_at=100.0,
        updated_at=102.0,
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
        status="running",
        current_stage="chunk",
        progress=0.5,
        created_at=100.0,
        updated_at=102.0,
        started_at=101.0,
    )
    repo.create_upload(document, version, binding, job)
    snapshot = DocumentParseSnapshot(
        snapshot_id="parse_001",
        document_version_id=version.version_id,
        parser_name="fake",
        parser_version="test",
        source_sha256=version.sha256,
        pages=[
            ParsedPage(
                page_number=1,
                text="境外接收方责任",
                extraction_method="native",
            )
        ],
        parsed_at=102.0,
    )
    repo._snapshots[version.version_id] = snapshot
    times = iter([103.0, 104.0, 105.0])
    worker = EvidenceIndexWorker(
        document_repo=repo,
        chunker=FakeEvidenceChunker(),
        evidence_index=index,
        embedder=FakeEmbed(dim=2),
        clock=lambda: next(times),
    )
    return worker, repo, index, job


class TestEvidenceIndexWorker:
    def test_success_completes_job_and_document(self) -> None:
        worker, repo, index, job = _setup()
        result = worker.run(job.job_id)
        assert result.document.status == "ready"
        assert result.job.status == "completed"
        assert result.job.current_stage == "ready"
        assert result.job.progress == 1.0
        assert len(result.chunks) == 1
        assert index.count_version("ver_001") == 1
        assert repo.get("doc_001").status == "ready"  # type: ignore[union-attr]
        assert repo.get_job("job_001").status == "completed"  # type: ignore[union-attr]

    def test_replay_completed_job_is_idempotent(self) -> None:
        worker, _, _, job = _setup()
        first = worker.run(job.job_id)
        second = worker.run(job.job_id)
        assert second.document == first.document
        assert second.job == first.job
        assert second.chunks == []

    def test_embedding_failure_preserves_resumable_stage(self) -> None:
        class FailingEmbedder:
            def embed(self, texts: list[str]) -> list[list[float]]:
                raise RuntimeError("embedding unavailable")

        worker, repo, index, job = _setup()
        failing_worker = EvidenceIndexWorker(
            document_repo=repo,
            chunker=FakeEvidenceChunker(),
            evidence_index=index,
            embedder=FailingEmbedder(),
            clock=iter([103.0, 104.0]).__next__,
        )

        with pytest.raises(RuntimeError, match="embedding unavailable"):
            failing_worker.run(job.job_id)

        failed_document = repo.get("doc_001")
        failed_job = repo.get_job(job.job_id)
        assert failed_document is not None
        assert failed_document.status == "embedding"
        assert failed_job is not None
        assert failed_job.status == "running"
        assert failed_job.current_stage == "embedding"
        assert failed_job.error_code is None

    def test_zero_vector_fallback_is_rejected_but_remains_resumable(self) -> None:
        class ZeroEmbedder:
            def embed(self, texts: list[str]) -> list[list[float]]:
                return [[0.0, 0.0] for _text in texts]

        worker, repo, index, job = _setup()
        zero_worker = EvidenceIndexWorker(
            document_repo=repo,
            chunker=FakeEvidenceChunker(),
            evidence_index=index,
            embedder=ZeroEmbedder(),
            clock=iter([103.0, 104.0]).__next__,
        )

        with pytest.raises(RuntimeError, match="零向量"):
            zero_worker.run(job.job_id)

        failed_job = repo.get_job(job.job_id)
        assert failed_job is not None
        assert failed_job.status == "running"
        assert failed_job.current_stage == "embedding"
