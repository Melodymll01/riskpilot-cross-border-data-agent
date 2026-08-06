"""PageEvidenceChunker 测试。"""

from __future__ import annotations

from domain import (
    CaseDocument,
    Document,
    DocumentParseSnapshot,
    DocumentVersion,
    EvidenceChunkerPort,
    ParsedPage,
    ParsedTable,
)
from infra.evidence import PageEvidenceChunker


def _objects():
    document = Document(
        document_id="doc_001",
        workspace_id="ws_001",
        logical_name="contract.pdf",
        document_type="contract",
        status="chunking",
        created_by="github:alice",
        current_version_id="ver_001",
        created_at=100.0,
        updated_at=101.0,
    )
    version = DocumentVersion(
        version_id="ver_001",
        document_id=document.document_id,
        version_number=1,
        object_key="objects/source.pdf",
        sha256="a" * 64,
        mime_type="application/pdf",
        size_bytes=100,
        parser_version="1.0.0",
        page_count=2,
        created_at=100.0,
    )
    snapshot = DocumentParseSnapshot(
        snapshot_id="parse_001",
        document_version_id=version.version_id,
        parser_name="parser",
        parser_version="1.0.0",
        source_sha256=version.sha256,
        pages=[
            ParsedPage(
                page_number=1,
                text="第一页关于境外接收方责任。" * 6,
                extraction_method="native",
            ),
            ParsedPage(
                page_number=2,
                text="第二页关于数据安全措施。",
                extraction_method="native",
                tables=[
                    ParsedTable(
                        table_id="t1",
                        page_number=2,
                        markdown="| 字段 | 值 |\n| --- | --- |\n| 地区 | EU |",
                        row_count=2,
                        column_count=2,
                    )
                ],
            ),
        ],
        parsed_at=101.0,
    )
    bindings = [
        CaseDocument(
            case_id="case_001",
            document_id=document.document_id,
            added_by="github:alice",
            added_at=100.0,
        ),
        CaseDocument(
            case_id="case_002",
            document_id=document.document_id,
            added_by="github:alice",
            added_at=100.0,
        ),
    ]
    return document, version, snapshot, bindings


class TestPageEvidenceChunker:
    def test_satisfies_port(self) -> None:
        chunker = PageEvidenceChunker(
            chunk_size=80,
            chunk_overlap=10,
            clock=lambda: 102.0,
        )
        assert isinstance(chunker, EvidenceChunkerPort)

    def test_never_crosses_page_boundaries_and_expands_bindings(self) -> None:
        document, version, snapshot, bindings = _objects()
        counter = iter(range(100))
        chunker = PageEvidenceChunker(
            chunk_size=80,
            chunk_overlap=10,
            id_factory=lambda: f"evc_{next(counter)}",
            clock=lambda: 102.0,
        )
        chunks = chunker.chunk(document, version, snapshot, bindings)
        assert {chunk.case_id for chunk in chunks} == {"case_001", "case_002"}
        assert {chunk.page_number for chunk in chunks} == {1, 2}
        assert all(chunk.workspace_id == "ws_001" for chunk in chunks)
        assert all(chunk.source_sha256 == version.sha256 for chunk in chunks)
        page_two = [chunk for chunk in chunks if chunk.page_number == 2]
        assert any("| 地区 | EU |" in chunk.text for chunk in page_two)
        for case_id in {"case_001", "case_002"}:
            indices = [chunk.chunk_index for chunk in chunks if chunk.case_id == case_id]
            assert indices == list(range(len(indices)))
