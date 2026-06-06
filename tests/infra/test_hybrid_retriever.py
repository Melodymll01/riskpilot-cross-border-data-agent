"""HybridRetrieverAdapter 的契约 + 数据转换测试。"""

from __future__ import annotations

from typing import Any

from domain.models import Chunk
from domain.ports import RetrievePort
from infra.search import HybridRetrieverAdapter
from infra.search.hybrid_retriever import _dict_to_chunk


class _StubRetriever:
    def __init__(self, results: list[dict[str, Any]] | None = None) -> None:
        self.results = list(results) if results else []
        self.calls: list[tuple[str, int]] = []
        self.viewers_calls: list[Any] = []

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        *,
        viewers: Any = None,
    ) -> list[dict[str, Any]]:
        self.calls.append((query, top_k))
        self.viewers_calls.append(viewers)
        return self.results[:top_k]


# ── 契约 ────────────────────────────────────────────────────────────


class TestProtocolConformance:
    def test_implements_retrieve_port(self) -> None:
        adapter = HybridRetrieverAdapter(retriever=_StubRetriever())
        assert isinstance(adapter, RetrievePort)


# ── _dict_to_chunk 转换 ─────────────────────────────────────────────


class TestDictToChunk:
    def test_basic_distance_to_score(self) -> None:
        chunk = _dict_to_chunk(
            {
                "id": "c1",
                "text": "条文内容",
                "distance": 0.3,
                "metadata": {
                    "source_type": "law",
                    "source_name": "PIPL",
                    "title": "第38条",
                    "category": "法规",
                },
            }
        )
        assert isinstance(chunk, Chunk)
        assert chunk.chunk_id == "c1"
        assert chunk.title == "第38条"
        # distance=0.3 → score=0.7
        assert abs(chunk.score - 0.7) < 1e-9

    def test_rerank_score_takes_precedence(self) -> None:
        chunk = _dict_to_chunk(
            {
                "id": "c2",
                "text": "x",
                "distance": 0.2,
                "rrf_score": 0.5,
                "rerank_score": 0.9,
                "metadata": {"source_name": "PIPL"},
            }
        )
        assert chunk.score == 0.9

    def test_rrf_score_used_when_no_rerank(self) -> None:
        chunk = _dict_to_chunk(
            {
                "id": "c3",
                "text": "x",
                "distance": 0.5,
                "rrf_score": 0.42,
                "metadata": {"source_name": "PIPL"},
            }
        )
        assert chunk.score == 0.42

    def test_match_type_preserved_in_metadata(self) -> None:
        chunk = _dict_to_chunk(
            {
                "id": "c4",
                "text": "x",
                "distance": 0.2,
                "match_type": "bm25",
                "bm25_score": 3.4,
                "fused_from": ["bm25", "vector"],
                "metadata": {"source_name": "PIPL", "chunk_index": 7},
            }
        )
        assert chunk.metadata["match_type"] == "bm25"
        assert chunk.metadata["bm25_score"] == 3.4
        assert chunk.metadata["fused_from"] == ["bm25", "vector"]
        assert chunk.metadata["chunk_index"] == 7

    def test_missing_text_falls_back_to_original(self) -> None:
        chunk = _dict_to_chunk(
            {
                "id": "c5",
                "original_text": "原文",
                "distance": 0.0,
                "metadata": {"source_name": "PIPL"},
            }
        )
        assert chunk.text == "原文"

    def test_defaults_when_metadata_minimal(self) -> None:
        chunk = _dict_to_chunk({"id": "c6", "text": "x", "metadata": {}})
        assert chunk.source_type == "law"
        assert chunk.source_name == "unknown"
        assert chunk.title == ""
        assert chunk.score == 0.0


# ── 端到端委托 ──────────────────────────────────────────────────────


class TestRetrieveDelegation:
    def test_retrieve_passes_top_k(self) -> None:
        stub = _StubRetriever(
            results=[
                {
                    "id": f"c{i}",
                    "text": f"text-{i}",
                    "distance": 0.1 * i,
                    "metadata": {"source_name": "PIPL"},
                }
                for i in range(5)
            ]
        )
        adapter = HybridRetrieverAdapter(retriever=stub)
        result = adapter.retrieve("数据出境", top_k=3)
        assert len(result) == 3
        assert all(isinstance(c, Chunk) for c in result)
        assert stub.calls == [("数据出境", 3)]

    def test_retrieve_empty(self) -> None:
        adapter = HybridRetrieverAdapter(retriever=_StubRetriever())
        assert adapter.retrieve("query") == []

    def test_extra_args_currently_ignored(self) -> None:
        """corpus / owner_id / filters 当前阶段应能传入但不报错。"""
        stub = _StubRetriever(
            results=[
                {"id": "c1", "text": "x", "distance": 0.1, "metadata": {"source_name": "PIPL"}}
            ]
        )
        adapter = HybridRetrieverAdapter(retriever=stub)
        result = adapter.retrieve(
            "q",
            top_k=1,
            corpus="user_docs",
            owner_id="github:alice",
            filters={"category": "法规"},
        )
        assert len(result) == 1
