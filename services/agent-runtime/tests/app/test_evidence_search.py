"""EvidenceSearchUseCase 权限与作用域测试。"""

from __future__ import annotations

from app.use_cases import EvidenceSearchUseCase
from domain.evidence import EvidenceChunk
from tests.fakes import FakeEmbed, FakeEvidenceIndex


def _setup():
    index = FakeEvidenceIndex()
    chunk = EvidenceChunk(
        chunk_id="evc_001",
        owner_id="github:alice",
        document_id="doc_001",
        document_version_id="ver_001",
        page_number=1,
        chunk_index=0,
        text="境外接收方责任",
        source_sha256="a" * 64,
        created_at=100.0,
    )
    index.chunks[chunk.chunk_id] = (chunk, [1.0])
    use_case = EvidenceSearchUseCase(
        evidence_index=index,
        embedder=FakeEmbed(dim=1),
    )
    return use_case, index


class TestEvidenceSearch:
    def test_member_searches_only_case_scope(self) -> None:
        use_case, index = _setup()
        hits = use_case.search(
            "github:alice",
            query="责任",
        )
        assert [hit.chunk.chunk_id for hit in hits] == ["evc_001"]
        assert index.search_calls[0]["owner_id"] == "github:alice"

    def test_outsider_gets_case_not_found(self) -> None:
        use_case, _ = _setup()
        assert use_case.search("github:outsider", query="责任") == []
