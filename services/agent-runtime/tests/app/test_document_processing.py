import base64
import hashlib
import time

from app.document_processing import InternalDocumentService
from domain.runtime import DocumentProcess
from infra.document_processing import RiskPilotDocumentParser
from infra.evidence import PageEvidenceChunker
from tests.fakes import (
    FakeDocumentLoader,
    FakeEmbed,
    FakeEvidenceIndex,
    FakeKbRepo,
    FakeParseArtifactStore,
)


def _service():
    artifacts = FakeParseArtifactStore()
    evidence = FakeEvidenceIndex()
    kb = FakeKbRepo()
    service = InternalDocumentService(
        loader=FakeDocumentLoader(),
        parser=RiskPilotDocumentParser(clock=time.time),
        chunker=PageEvidenceChunker(
            chunk_size=100,
            chunk_overlap=10,
            clock=time.time,
        ),
        embedder=FakeEmbed(dim=4),
        kb_repo=kb,
        parse_artifacts=artifacts,
        evidence_index=evidence,
    )
    return service, artifacts, evidence, kb


def _command(content: bytes) -> DocumentProcess:
    return DocumentProcess(
        schema_version="1.0",
        operation="ingest",
        document_id="doc-1",
        version_id="ver-1",
        actor_id="github:alice",
        owner_id="github:alice",
        source_name="policy.txt",
        source_type="file",
        source_url=None,
        category="law",
        mime_type="text/plain",
        sha256=hashlib.sha256(content).hexdigest(),
        content_base64=base64.b64encode(content).decode(),
    )


def test_ingest_and_delete_touch_only_runtime_artifacts() -> None:
    service, artifacts, evidence, kb = _service()
    command = _command("跨境数据合规材料".encode())

    ready = service.process(command)
    assert ready.status == "ready"
    assert ready.chunk_count == 2
    assert len(artifacts.items) == 1
    assert evidence.count_version("ver-1") > 0
    assert kb.count_chunks() == 2

    deleted = service.process(
        command.model_copy(update={"operation": "delete", "content_base64": None})
    )
    assert deleted.status == "deleted"
    assert artifacts.items == {}
    assert evidence.count_version("ver-1") == 0
    assert kb.count_chunks() == 0


def test_ingest_sha_mismatch_fails_without_artifact_writes() -> None:
    service, artifacts, evidence, kb = _service()
    command = _command(b"hello").model_copy(update={"sha256": "a" * 64})

    result = service.process(command)
    assert result.status == "failed"
    assert result.error_code == "ValueError"
    assert artifacts.items == {}
    assert evidence.count_version("ver-1") == 0
    assert kb.count_chunks() == 0
