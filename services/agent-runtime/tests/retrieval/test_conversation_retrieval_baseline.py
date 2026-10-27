"""Real Chroma + BM25/RRF retrieval, with deterministic query vectors and no model calls."""

from __future__ import annotations

from config import settings
from infra.search.hybrid_retriever import HybridRetrieverAdapter
from processing.metadata import ChunkWithMetadata
from retrieval.search.bm25_index import BM25Index
from retrieval.search.reranker import DistanceThresholdReranker
from retrieval.search.retriever import Retriever
from retrieval.search.vector_store import VectorStore


class QueryVector:
    def embed_query(self, query):
        return [1.0, 0.0, 0.0, 0.0]


class OriginalQuery:
    def rewrite(self, query):
        return [query]


def test_actual_hybrid_retrieval_separates_public_law_and_user_documents(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "chroma_persist_dir", str(tmp_path))
    monkeypatch.setattr(settings, "enable_bm25_rrf", True)
    store = VectorStore()
    chunks = [
        ChunkWithMetadata(
            chunk_id=key,
            text="境外接收方应采取安全措施 " + key,
            source_type="file",
            source_name=key + ".txt",
            title=key,
            owner_id=owner,
        )
        for key, owner in [("law", None), ("alice", "alice"), ("bob", "bob")]
    ]
    store.add_chunks(chunks, [[1.0, 0.0, 0.0, 0.0]] * 3)
    adapter = HybridRetrieverAdapter(
        Retriever(
            embedder=QueryVector(),
            vector_store=store,
            reranker=DistanceThresholdReranker(),
            query_rewriter=OriginalQuery(),
        )
    )
    law = adapter.retrieve("境外接收方", corpus="law", owner_id="alice")
    private = adapter.retrieve("境外接收方", corpus="user_docs", owner_id="alice")
    assert [hit.source_name for hit in law] == ["law.txt"]
    assert [hit.source_name for hit in private] == ["alice.txt"]
    assert adapter.retrieve("境外接收方", corpus="user_docs", owner_id="outsider") == []


def test_bm25_filters_scope_before_top_k_ranking(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "chroma_persist_dir", str(tmp_path))
    store = VectorStore()
    chunks = [
        ChunkWithMetadata(
            chunk_id=f"bob-{index}",
            text="申报流程 完备性查验 " * 8,
            source_type="file",
            source_name=f"bob-{index}.txt",
            title="bob",
            owner_id="bob",
        )
        for index in range(6)
    ]
    chunks.extend(
        ChunkWithMetadata(
            chunk_id=f"neutral-{index}",
            text="其他法规内容",
            source_type="file",
            source_name=f"neutral-{index}.txt",
            title="neutral",
            owner_id="bob",
        )
        for index in range(10)
    )
    chunks.append(
        ChunkWithMetadata(
            chunk_id="alice-0",
            text="申报流程",
            source_type="file",
            source_name="alice.txt",
            title="alice",
            owner_id="alice",
        )
    )
    store.add_chunks(chunks, [[1.0, 0.0, 0.0, 0.0]] * len(chunks))

    result = BM25Index(store).search("申报流程", top_k=1, viewers=["alice"])

    assert [item["id"] for item in result] == ["alice-0"]
    assert result[0]["bm25_rank"] == 1
