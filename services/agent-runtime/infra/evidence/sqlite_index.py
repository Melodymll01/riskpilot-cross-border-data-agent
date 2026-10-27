"""Owner/version-scoped SQLite evidence index for Python-owned RAG artifacts."""

from __future__ import annotations

import json
import math
from collections import Counter
from typing import Any

from domain.evidence import EvidenceChunk, EvidenceSearchHit
from infra.storage._db import SqliteConnectionPool

_RRF_K = 60
_SCHEMA = """
CREATE TABLE IF NOT EXISTS evidence_chunks (
    chunk_id            TEXT PRIMARY KEY,
    owner_id            TEXT NOT NULL,
    document_id         TEXT NOT NULL,
    document_version_id TEXT NOT NULL,
    page_number         INTEGER NOT NULL,
    chunk_index         INTEGER NOT NULL,
    text                TEXT NOT NULL,
    source_sha256       TEXT NOT NULL,
    embedding_json      TEXT NOT NULL,
    created_at          REAL NOT NULL,
    UNIQUE (document_version_id, page_number, chunk_index)
);
CREATE INDEX IF NOT EXISTS idx_evidence_chunks_scope
    ON evidence_chunks(owner_id, document_id, document_version_id);
CREATE TABLE IF NOT EXISTS evidence_embedding_profile (
    singleton  INTEGER PRIMARY KEY CHECK(singleton=1),
    identity   TEXT NOT NULL,
    dimensions INTEGER NOT NULL
);
"""


class SqliteEvidenceIndex:
    def __init__(
        self, pool: SqliteConnectionPool, *, embedding_identity: str | None = None
    ) -> None:
        self._pool = pool
        self._embedding_identity = embedding_identity
        with pool.get() as conn:
            conn.executescript(_SCHEMA)

    def replace_version_chunks(
        self,
        document_version_id: str,
        chunks: list[EvidenceChunk],
        embeddings: list[list[float]],
    ) -> None:
        _validate_index_payload(document_version_id, chunks, embeddings)
        conn = self._pool.get()
        with conn:
            _validate_parse_artifact_scope(conn, chunks)
            self._validate_embedding_identity(conn, len(embeddings[0]), writing=True)
            conn.execute(
                "DELETE FROM evidence_chunks WHERE document_version_id = ?",
                (document_version_id,),
            )
            conn.executemany(
                """INSERT INTO evidence_chunks
                   (chunk_id, owner_id, document_id, document_version_id, page_number,
                    chunk_index, text, source_sha256, embedding_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        chunk.chunk_id,
                        chunk.owner_id,
                        chunk.document_id,
                        chunk.document_version_id,
                        chunk.page_number,
                        chunk.chunk_index,
                        chunk.text,
                        chunk.source_sha256,
                        json.dumps(embedding),
                        chunk.created_at,
                    )
                    for chunk, embedding in zip(chunks, embeddings, strict=True)
                ],
            )

    def search(
        self,
        *,
        owner_id: str,
        query: str,
        query_embedding: list[float],
        top_k: int = 5,
        document_ids: list[str] | None = None,
        document_version_ids: list[str] | None = None,
    ) -> list[EvidenceSearchHit]:
        if not owner_id or not query.strip() or top_k < 1:
            raise ValueError("owner_id/query are required and top_k must be positive")
        sql = "SELECT * FROM evidence_chunks WHERE owner_id = ?"
        params: list[object] = [owner_id]
        if document_ids is not None:
            if not document_ids:
                return []
            sql += " AND document_id IN (" + ",".join("?" for _ in document_ids) + ")"
            params.extend(document_ids)
        if document_version_ids is not None:
            if not document_version_ids:
                return []
            sql += (
                " AND document_version_id IN (" + ",".join("?" for _ in document_version_ids) + ")"
            )
            params.extend(document_version_ids)
        rows = self._pool.get().execute(sql, params).fetchall()
        if rows:
            self._validate_embedding_identity(self._pool.get(), len(query_embedding))
        return _rank_rows(rows, query=query, query_embedding=query_embedding, top_k=top_k)

    def count_version(self, document_version_id: str) -> int:
        row = (
            self._pool.get()
            .execute(
                "SELECT COUNT(*) AS n FROM evidence_chunks WHERE document_version_id = ?",
                (document_version_id,),
            )
            .fetchone()
        )
        return int(row["n"])

    def delete_version(self, document_version_id: str) -> int:
        conn = self._pool.get()
        with conn:
            cursor = conn.execute(
                "DELETE FROM evidence_chunks WHERE document_version_id = ?",
                (document_version_id,),
            )
        return cursor.rowcount

    def _validate_embedding_identity(
        self, conn: Any, dimensions: int, *, writing: bool = False
    ) -> None:
        if self._embedding_identity is None:
            return
        row = conn.execute(
            "SELECT identity, dimensions FROM evidence_embedding_profile WHERE singleton=1"
        ).fetchone()
        if row is None:
            if conn.execute("SELECT 1 FROM evidence_chunks LIMIT 1").fetchone():
                raise ValueError("已有证据索引缺少 Embedding 模型信息，请显式重建索引")
            if writing:
                conn.execute(
                    "INSERT OR IGNORE INTO evidence_embedding_profile VALUES (1, ?, ?)",
                    (self._embedding_identity, dimensions),
                )
                row = conn.execute(
                    "SELECT identity, dimensions FROM evidence_embedding_profile WHERE singleton=1"
                ).fetchone()
        if row is not None and (row[0] != self._embedding_identity or row[1] != dimensions):
            raise ValueError("Embedding 模型或维度与证据索引不一致，请使用原配置或重建索引")


def _validate_index_payload(
    document_version_id: str,
    chunks: list[EvidenceChunk],
    embeddings: list[list[float]],
) -> None:
    if not chunks:
        raise ValueError("chunks cannot be empty")
    if len(chunks) != len(embeddings):
        raise ValueError("chunks and embeddings must have equal length")
    if any(chunk.document_version_id != document_version_id for chunk in chunks):
        raise ValueError("all chunks must belong to the immutable document version")
    dimensions = {len(embedding) for embedding in embeddings}
    if 0 in dimensions or len(dimensions) != 1:
        raise ValueError("embedding dimensions must be non-zero and consistent")


def _validate_parse_artifact_scope(conn: Any, chunks: list[EvidenceChunk]) -> None:
    for chunk in chunks:
        row = conn.execute(
            "SELECT owner_id, document_id, source_sha256 FROM parse_artifacts "
            "WHERE document_version_id = ?",
            (chunk.document_version_id,),
        ).fetchone()
        if (
            row is None
            or row["document_id"] != chunk.document_id
            or row["owner_id"] != chunk.owner_id
            or row["source_sha256"] != chunk.source_sha256
        ):
            raise ValueError("evidence chunk does not match its immutable parse artifact")


def _rank_rows(
    rows: list[Any],
    *,
    query: str,
    query_embedding: list[float],
    top_k: int,
) -> list[EvidenceSearchHit]:
    if not rows:
        return []
    candidates = [_row_to_candidate(row) for row in rows]
    if any(len(candidate["embedding"]) != len(query_embedding) for candidate in candidates):
        raise ValueError("query embedding dimensions do not match the index")
    vector_ranking = sorted(
        candidates,
        key=lambda candidate: _cosine_similarity(query_embedding, candidate["embedding"]),
        reverse=True,
    )
    vector_scores = {
        candidate["chunk"].chunk_id: _cosine_similarity(query_embedding, candidate["embedding"])
        for candidate in vector_ranking
    }
    bm25_scores = _bm25_scores(query, candidates)
    bm25_ranking = sorted(
        candidates,
        key=lambda candidate: bm25_scores[candidate["chunk"].chunk_id],
        reverse=True,
    )
    vector_ranks = {
        candidate["chunk"].chunk_id: rank for rank, candidate in enumerate(vector_ranking, start=1)
    }
    bm25_ranks = {
        candidate["chunk"].chunk_id: rank
        for rank, candidate in enumerate(bm25_ranking, start=1)
        if bm25_scores[candidate["chunk"].chunk_id] > 0
    }
    hits: list[EvidenceSearchHit] = []
    for candidate in candidates:
        chunk = candidate["chunk"]
        score = 1.0 / (_RRF_K + vector_ranks[chunk.chunk_id])
        if chunk.chunk_id in bm25_ranks:
            score += 1.0 / (_RRF_K + bm25_ranks[chunk.chunk_id])
        hits.append(
            EvidenceSearchHit(
                chunk=chunk,
                score=score,
                vector_score=vector_scores[chunk.chunk_id],
                bm25_score=bm25_scores[chunk.chunk_id],
            )
        )
    hits.sort(key=lambda hit: (hit.score, hit.vector_score), reverse=True)
    return hits[:top_k]


def _row_to_candidate(row: Any) -> dict[str, Any]:
    return {
        "chunk": EvidenceChunk(
            chunk_id=row["chunk_id"],
            owner_id=row["owner_id"],
            document_id=row["document_id"],
            document_version_id=row["document_version_id"],
            page_number=row["page_number"],
            chunk_index=row["chunk_index"],
            text=row["text"],
            source_sha256=row["source_sha256"],
            created_at=row["created_at"],
        ),
        "embedding": [float(value) for value in json.loads(row["embedding_json"])],
    }


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    score = sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)
    return max(-1.0, min(1.0, score))


def _bm25_scores(query: str, candidates: list[dict[str, Any]]) -> dict[str, float]:
    query_tokens = _tokenize(query)
    documents = [_tokenize(candidate["chunk"].text) for candidate in candidates]
    if not query_tokens or not documents:
        return {candidate["chunk"].chunk_id: 0.0 for candidate in candidates}
    doc_count = len(documents)
    avg_length = sum(len(tokens) for tokens in documents) / doc_count or 1.0
    document_frequency = Counter(token for tokens in documents for token in set(tokens))
    result: dict[str, float] = {}
    for candidate, tokens in zip(candidates, documents, strict=True):
        frequencies = Counter(tokens)
        score = 0.0
        for token in query_tokens:
            frequency = frequencies[token]
            if frequency == 0:
                continue
            inverse_frequency = math.log(
                1.0
                + (doc_count - document_frequency[token] + 0.5) / (document_frequency[token] + 0.5)
            )
            denominator = frequency + 1.5 * (1.0 - 0.75 + 0.75 * len(tokens) / avg_length)
            score += inverse_frequency * frequency * 2.5 / denominator
        result[candidate["chunk"].chunk_id] = score
    return result


def _tokenize(text: str) -> list[str]:
    import jieba

    return [
        token for token in (part.strip().lower() for part in jieba.cut_for_search(text)) if token
    ]
