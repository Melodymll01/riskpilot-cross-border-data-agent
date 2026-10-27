from pathlib import Path

import pytest

from domain.document_content import DocumentParseSnapshot, ParsedPage
from domain.evidence import EvidenceChunk
from domain.ports import EvidenceIndexPort
from infra.evidence import SqliteEvidenceIndex
from infra.storage import SqliteParseArtifactStore
from infra.storage._db import SqliteConnectionPool


@pytest.fixture
def pool(tmp_path: Path) -> SqliteConnectionPool:
    return SqliteConnectionPool(str(tmp_path / "evidence.db"))


@pytest.fixture
def index(pool: SqliteConnectionPool) -> SqliteEvidenceIndex:
    SqliteParseArtifactStore(pool)
    return SqliteEvidenceIndex(pool)


def _chunk(chunk_id: str, *, owner_id: str, text: str) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        owner_id=owner_id,
        document_id=f"doc_{chunk_id}",
        document_version_id=f"ver_{chunk_id}",
        page_number=1,
        chunk_index=0,
        text=text,
        source_sha256="a" * 64,
        created_at=100.0,
    )


def _seed_scope(pool: SqliteConnectionPool, chunk: EvidenceChunk) -> None:
    SqliteParseArtifactStore(pool).save(
        owner_id=chunk.owner_id,
        document_id=chunk.document_id,
        document_version_id=chunk.document_version_id,
        source_sha256=chunk.source_sha256,
        snapshot=DocumentParseSnapshot(
            snapshot_id=f"parse_{chunk.chunk_id}",
            document_version_id=chunk.document_version_id,
            parser_name="test",
            parser_version="1.0",
            source_sha256=chunk.source_sha256,
            pages=[
                ParsedPage(
                    page_number=1,
                    text=chunk.text,
                    extraction_method="native",
                )
            ],
            parsed_at=100.0,
        ),
    )


def test_satisfies_port(index: SqliteEvidenceIndex) -> None:
    assert isinstance(index, EvidenceIndexPort)


def test_sql_scope_filter_prevents_cross_owner_leak(
    pool: SqliteConnectionPool,
    index: SqliteEvidenceIndex,
) -> None:
    chunks = [
        _chunk("a", owner_id="owner-1", text="普通说明"),
        _chunk("b", owner_id="owner-1", text="境外接收方责任高度匹配"),
        _chunk("c", owner_id="owner-2", text="境外接收方责任更高匹配"),
    ]
    for chunk in chunks:
        _seed_scope(pool, chunk)
        index.replace_version_chunks(chunk.document_version_id, [chunk], [[1.0, 0.0]])
    hits = index.search(
        owner_id="owner-1",
        query="境外接收方责任",
        query_embedding=[1.0, 0.0],
        top_k=5,
    )
    assert {hit.chunk.chunk_id for hit in hits} == {"a", "b"}


def test_hybrid_ranking_uses_bm25_signal(
    pool: SqliteConnectionPool,
    index: SqliteEvidenceIndex,
) -> None:
    keyword = _chunk("keyword", owner_id="owner-1", text="境外接收方必须承担安全保护责任")
    vector = _chunk("vector", owner_id="owner-1", text="无关文本")
    for chunk, embedding in ((keyword, [0.8, 0.2]), (vector, [1.0, 0.0])):
        _seed_scope(pool, chunk)
        index.replace_version_chunks(chunk.document_version_id, [chunk], [embedding])
    hits = index.search(
        owner_id="owner-1",
        query="境外接收方责任",
        query_embedding=[1.0, 0.0],
        top_k=2,
    )
    assert hits[0].chunk.chunk_id == "keyword"
    assert hits[0].bm25_score > 0


def test_search_honors_immutable_version_scope(
    pool: SqliteConnectionPool,
    index: SqliteEvidenceIndex,
) -> None:
    old = _chunk("old", owner_id="owner-1", text="旧版本重要数据")
    current = old.model_copy(
        update={
            "chunk_id": "current",
            "document_version_id": "ver_current",
            "text": "当前版本个人信息",
            "source_sha256": "b" * 64,
        }
    )
    for chunk, embedding in ((old, [1.0, 0.0]), (current, [0.0, 1.0])):
        _seed_scope(pool, chunk)
        index.replace_version_chunks(chunk.document_version_id, [chunk], [embedding])
    hits = index.search(
        owner_id="owner-1",
        query="重要数据",
        query_embedding=[1.0, 0.0],
        document_version_ids=["ver_current"],
    )
    assert [hit.chunk.chunk_id for hit in hits] == ["current"]


def test_rejects_chunk_not_bound_to_parse_artifact(
    pool: SqliteConnectionPool,
    index: SqliteEvidenceIndex,
) -> None:
    chunk = _chunk("orphan", owner_id="owner-1", text="orphan")
    _seed_scope(pool, chunk.model_copy(update={"owner_id": "owner-2"}))
    with pytest.raises(ValueError, match="immutable parse artifact"):
        index.replace_version_chunks(chunk.document_version_id, [chunk], [[1.0]])


def test_replace_and_delete_version_are_idempotent(
    pool: SqliteConnectionPool,
    index: SqliteEvidenceIndex,
) -> None:
    old = _chunk("old", owner_id="owner-1", text="old")
    new = old.model_copy(update={"chunk_id": "new", "text": "new"})
    _seed_scope(pool, old)
    index.replace_version_chunks(old.document_version_id, [old], [[1.0]])
    index.replace_version_chunks(old.document_version_id, [new], [[1.0]])
    assert index.count_version(old.document_version_id) == 1
    assert index.delete_version(old.document_version_id) == 1
    assert index.delete_version(old.document_version_id) == 0
