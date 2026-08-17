"""DocumentPipelineWorker 可恢复状态机测试。"""

from __future__ import annotations

import hashlib

from app.workers import (
    DocumentOcrWorker,
    DocumentPipelineWorker,
    DocumentProcessingWorker,
    EvidenceIndexWorker,
)
from domain import (
    CaseDocument,
    Document,
    DocumentParseSnapshot,
    DocumentVersion,
    ParsedPage,
    ProcessingJob,
)
from tests.fakes import (
    FakeDocumentOcr,
    FakeDocumentParser,
    FakeEmbed,
    FakeEvidenceChunker,
    FakeEvidenceIndex,
    FakeObjectStore,
    InMemoryDocumentRepo,
)


def _setup(*, requires_ocr: bool = False):
    repo = InMemoryDocumentRepo()
    objects = FakeObjectStore()
    content = b"text"
    version = DocumentVersion(
        version_id="ver_001",
        document_id="doc_001",
        version_number=1,
        object_key="ws/doc/ver/source.txt",
        sha256=hashlib.sha256(content).hexdigest(),
        mime_type="application/pdf" if requires_ocr else "text/plain",
        size_bytes=len(content),
        created_at=100.0,
    )
    document = Document(
        document_id="doc_001",
        workspace_id="ws_001",
        logical_name="source.pdf" if requires_ocr else "source.txt",
        document_type="case_material",
        status="queued",
        created_by="github:alice",
        current_version_id=version.version_id,
        created_at=100.0,
        updated_at=100.0,
    )
    job = ProcessingJob(
        job_id="job_001",
        document_version_id=version.version_id,
        current_stage="extract_structure",
        created_at=100.0,
        updated_at=100.0,
    )
    repo.create_upload(
        document,
        version,
        CaseDocument(
            case_id="case_001",
            document_id=document.document_id,
            added_by="github:alice",
            added_at=100.0,
        ),
        job,
    )
    objects.put(version.object_key, content)
    parser: FakeDocumentParser
    if requires_ocr:

        class EmptyPageParser(FakeDocumentParser):
            def parse(
                self,
                parsed_version: DocumentVersion,
                parsed_content: bytes,
            ) -> DocumentParseSnapshot:
                self.calls.append((parsed_version, parsed_content))
                return DocumentParseSnapshot(
                    snapshot_id="parse_ocr",
                    document_version_id=parsed_version.version_id,
                    parser_name="fake",
                    parser_version="test",
                    source_sha256=parsed_version.sha256,
                    pages=[ParsedPage(page_number=1, extraction_method="empty")],
                    parsed_at=101.0,
                )

        parser = EmptyPageParser()
    else:
        parser = FakeDocumentParser()
    ocr = FakeDocumentOcr()
    index = FakeEvidenceIndex(repo)
    times = iter([101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0])

    def clock() -> float:
        return next(times)

    pipeline = DocumentPipelineWorker(
        document_repo=repo,
        parser=DocumentProcessingWorker(
            document_repo=repo,
            object_store=objects,
            parser=parser,
            clock=clock,
        ),
        ocr=DocumentOcrWorker(
            document_repo=repo,
            object_store=objects,
            ocr=ocr,
            clock=clock,
        ),
        indexer=EvidenceIndexWorker(
            document_repo=repo,
            chunker=FakeEvidenceChunker(),
            evidence_index=index,
            embedder=FakeEmbed(dim=2),
            clock=clock,
        ),
    )
    return pipeline, repo, index, parser, ocr


def test_pipeline_completes_and_replay_is_idempotent() -> None:
    pipeline, repo, index, parser, _ocr = _setup()

    first = pipeline.run("job_001")
    second = pipeline.run("job_001")

    assert first.outcome == "completed"
    assert second == first
    assert repo.get_job("job_001").status == "completed"  # type: ignore[union-attr]
    assert repo.get("doc_001").status == "ready"  # type: ignore[union-attr]
    assert index.count_version("ver_001") == 1
    assert len(parser.calls) == 1


def test_pipeline_executes_ocr_before_index() -> None:
    pipeline, repo, _, _, ocr = _setup(requires_ocr=True)

    result = pipeline.run("job_001")

    assert result.outcome == "completed"
    assert ocr.calls == ["ver_001"]
    snapshot = repo.get_parse_snapshot("ver_001")
    assert snapshot is not None
    assert snapshot.pages[0].extraction_method == "ocr"


def test_pipeline_resumes_from_embedding_stage() -> None:
    pipeline, repo, index, _parser, _ocr = _setup()
    pipeline._parser.run_parse_stage("job_001")
    job = repo.get_job("job_001")
    document = repo.get("doc_001")
    assert job is not None and document is not None
    embedding_job = job.advance(stage="embedding", progress=0.65, at=103.0)
    embedding_document = document.transition_to("embedding", at=103.0)
    repo.update_processing_state(
        embedding_document,
        embedding_job,
        expected_revision=job.revision,
    )

    result = pipeline.run("job_001")

    assert result.outcome == "completed"
    assert index.count_version("ver_001") == 1


def test_pipeline_resumes_from_running_parse_stage() -> None:
    pipeline, repo, index, _parser, _ocr = _setup()
    job = repo.get_job("job_001")
    document = repo.get("doc_001")
    assert job is not None and document is not None
    running = job.start(stage="extract_structure", at=101.0)
    parsing = document.transition_to("parsing", at=101.0)
    repo.update_processing_state(
        parsing,
        running,
        expected_revision=job.revision,
    )

    result = pipeline.run("job_001")

    assert result.outcome == "completed"
    assert index.count_version("ver_001") == 1


def test_cancelled_job_is_not_executed() -> None:
    pipeline, repo, index, parser, ocr = _setup()
    job = repo.get_job("job_001")
    document = repo.get("doc_001")
    assert job is not None and document is not None
    cancelled_job = job.cancel(at=101.0)
    cancelled_document = document.transition_to("cancelled", at=101.0)
    repo.update_processing_state(
        cancelled_document,
        cancelled_job,
        expected_revision=job.revision,
    )

    result = pipeline.run("job_001")

    assert result.outcome == "cancelled"
    assert parser.calls == []
    assert ocr.calls == []
    assert index.count_version("ver_001") == 0
