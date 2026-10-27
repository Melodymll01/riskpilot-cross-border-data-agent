from domain.document_content import DocumentParseSnapshot, ParsedPage, ParsedTable
from domain.documents import DocumentVersion
from domain.ports import EvidenceChunkerPort
from infra.evidence import PageEvidenceChunker


def _objects():
    version = DocumentVersion(
        version_id="ver_001",
        document_id="doc_001",
        sha256="a" * 64,
        mime_type="application/pdf",
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
    return version, snapshot


def test_satisfies_port() -> None:
    chunker = PageEvidenceChunker(
        chunk_size=80,
        chunk_overlap=10,
        clock=lambda: 102.0,
    )
    assert isinstance(chunker, EvidenceChunkerPort)


def test_never_crosses_page_boundaries_and_uses_immutable_scope() -> None:
    version, snapshot = _objects()
    counter = iter(range(100))
    chunker = PageEvidenceChunker(
        chunk_size=80,
        chunk_overlap=10,
        id_factory=lambda: f"evc_{next(counter)}",
        clock=lambda: 102.0,
    )
    chunks = chunker.chunk("github:alice", "doc_001", version, snapshot)
    assert {chunk.page_number for chunk in chunks} == {1, 2}
    assert all(chunk.owner_id == "github:alice" for chunk in chunks)
    assert all(chunk.document_version_id == "ver_001" for chunk in chunks)
    assert all(chunk.source_sha256 == version.sha256 for chunk in chunks)
    page_two = [chunk for chunk in chunks if chunk.page_number == 2]
    assert any("| 地区 | EU |" in chunk.text for chunk in page_two)
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
