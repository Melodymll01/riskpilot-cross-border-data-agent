import asyncio

import pytest

from app.factories import build_embedder
from config import Settings
from domain.models import KbChunk
from infra.evidence import SqliteEvidenceIndex
from infra.kb import ChromaKbRepo
from infra.storage._db import SqliteConnectionPool
from retrieval.search.embedder import Embedder
from retrieval.search.vector_store import VectorStore
from tests.infra.test_sqlite_evidence_index import _chunk, _seed_scope


def configuration(tmp_path, **overrides):
    return Settings(
        _env_file=None,
        **{
            "llm_provider": "local",
            "embed_provider": "api",
            "openai_api_key": "test-key",
            "openai_api_base": "https://embedding.example/v1",
            "embedding_model": "model-a",
            "embedding_dimensions": 2,
            "chroma_persist_dir": str(tmp_path / "chroma"),
            **overrides,
        },
    )


def test_embedding_factory_uses_explicit_settings_and_isolates_cache(tmp_path, monkeypatch):
    class Cache(dict):
        def set(self, key, value):
            self[key] = value

    cache = Cache()
    monkeypatch.setattr(Embedder, "_init_disk_cache", lambda self: cache)
    # Use in-memory cache so this test does not write the application's disk cache.
    first = build_embedder(configuration(tmp_path))._embedder
    second = build_embedder(
        configuration(tmp_path, openai_api_base="https://other.example/v1")
    )._embedder
    try:
        assert first.model == "model-a"
        assert first.client.base_url.host == "embedding.example"
        assert second.client.base_url.host == "other.example"
        assert first._cache_key("问题") != second._cache_key("问题")
        first.embed_texts = lambda texts, **kwargs: [[1.0, 0.0]]
        second.embed_texts = lambda texts, **kwargs: [[0.0, 1.0]]
        assert first.embed_query("问题") == [1.0, 0.0]
        assert second.embed_query("问题") == [0.0, 1.0]
        assert asyncio.run(first.embed_query_async("问题")) == [1.0, 0.0]
    finally:
        first.client.close()
        second.client.close()
        asyncio.run(first.async_client.close())
        asyncio.run(second.async_client.close())


def test_embedding_identity_includes_chunk_profile(tmp_path):
    base = configuration(
        tmp_path, chunk_size=600, chunk_overlap=120, embedding_max_input_tokens=512
    )
    changed_chunking = configuration(tmp_path, chunk_size=420, chunk_overlap=84)
    changed_budget = configuration(tmp_path, embedding_max_input_tokens=480)

    assert base.embedding_identity != changed_chunking.embedding_identity
    assert base.embedding_identity != changed_budget.embedding_identity

    identity_base = configuration(tmp_path)
    changed_schema = configuration(tmp_path, index_schema_version="parent-child-v2")
    changed_input = configuration(tmp_path, embedding_input_strategy="raw-child-v1")
    assert identity_base.embedding_identity != changed_schema.embedding_identity
    assert identity_base.embedding_identity != changed_input.embedding_identity


def test_chroma_rejects_different_model_before_deleting_source(tmp_path):
    first = VectorStore(config=configuration(tmp_path))
    chunk = KbChunk(
        chunk_id="one",
        text="法规",
        source_type="web",
        source_name="source",
        title="law",
        chunk_index=0,
    )
    ChromaKbRepo(first).add_chunks([chunk], [[1.0, 0.0]])
    assert first.query([1.0, 0.0], top_k=1)[0]["id"] == "one"
    second = VectorStore(config=configuration(tmp_path, embedding_model="model-b"))
    with pytest.raises(ValueError, match="模型或维度"):
        ChromaKbRepo(second).add_chunks([chunk], [[0.0, 1.0]])
    assert first.collection.count() == 1
    with pytest.raises(ValueError, match="模型或维度"):
        second.query([1.0, 0.0], top_k=1)
    with pytest.raises(ValueError, match="模型或维度"):
        first.query([1.0, 0.0, 0.0], top_k=1)


def test_sqlite_index_records_model_and_actual_dimensions(tmp_path):
    pool = SqliteConnectionPool(str(tmp_path / "index.sqlite3"))
    config = configuration(tmp_path)
    index = SqliteEvidenceIndex(pool, embedding_identity=config.embedding_identity)
    chunk = _chunk("one", owner_id="alice", text="法规证据")
    _seed_scope(pool, chunk)
    try:
        index.replace_version_chunks(chunk.document_version_id, [chunk], [[1.0, 0.0]])
        assert index.search(owner_id="alice", query="法规", query_embedding=[1.0, 0.0])
        changed = SqliteEvidenceIndex(
            pool,
            embedding_identity=configuration(
                tmp_path, embedding_model="model-b"
            ).embedding_identity,
        )
        with pytest.raises(ValueError, match="模型或维度"):
            changed.search(owner_id="alice", query="法规", query_embedding=[1.0, 0.0])
        with pytest.raises(ValueError, match="模型或维度"):
            index.search(owner_id="alice", query="法规", query_embedding=[1.0, 0.0, 0.0])
    finally:
        pool.close()


def test_legacy_index_requires_explicit_rebuild(tmp_path):
    pool = SqliteConnectionPool(str(tmp_path / "legacy.sqlite3"))
    chunk = _chunk("one", owner_id="alice", text="法规证据")
    _seed_scope(pool, chunk)
    try:
        SqliteEvidenceIndex(pool).replace_version_chunks(
            chunk.document_version_id, [chunk], [[1.0, 0.0]]
        )
        current = SqliteEvidenceIndex(
            pool, embedding_identity=configuration(tmp_path).embedding_identity
        )
        with pytest.raises(ValueError, match="缺少 Embedding"):
            current.search(owner_id="alice", query="法规", query_embedding=[1.0, 0.0])
        assert current.count_version(chunk.document_version_id) == 1
    finally:
        pool.close()
