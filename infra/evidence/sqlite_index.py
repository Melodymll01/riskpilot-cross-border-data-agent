"""SQLite 案件证据混合索引。"""

from __future__ import annotations

import json
import math
from collections import Counter
from typing import Any

from domain.documents import Document, ProcessingJob
from domain.evidence import EvidenceChunk, EvidenceSearchHit
from infra.storage._db import SqliteConnectionPool

_RRF_K = 60


class SqliteEvidenceIndex:
    """SQL 先过滤作用域，再在候选集上计算向量 + BM25 + RRF。"""

    def __init__(
        self, pool: SqliteConnectionPool, *, embedding_identity: str | None = None
    ) -> None:
        self._pool = pool
        self._embedding_identity = embedding_identity
        pool.get().execute("""CREATE TABLE IF NOT EXISTS evidence_embedding_profile (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), identity TEXT NOT NULL,
            dimensions INTEGER NOT NULL)""")

    def _validate_embedding_identity(
        self, conn: Any, dimensions: int, *, writing: bool = False
    ) -> None:
        if self._embedding_identity is None:
            return
        row = conn.execute(
            "SELECT identity, dimensions FROM evidence_embedding_profile WHERE singleton=1"
        ).fetchone()
        if row is None:
            if conn.execute("SELECT 1 FROM chat_evidence_chunks LIMIT 1").fetchone():
                raise ValueError("已有附件索引缺少 Embedding 模型信息，请在新数据库重建索引")
            if writing:
                conn.execute(
                    "INSERT OR IGNORE INTO evidence_embedding_profile VALUES (1, ?, ?)",
                    (self._embedding_identity, dimensions),
                )
                row = conn.execute(
                    "SELECT identity, dimensions FROM evidence_embedding_profile WHERE singleton=1"
                ).fetchone()
        if row is not None and (row[0] != self._embedding_identity or row[1] != dimensions):
            raise ValueError("Embedding 模型或维度与附件索引不一致，请使用原配置或重建索引")

    def replace_version_chunks(
        self,
        document_version_id: str,
        chunks: list[EvidenceChunk],
        embeddings: list[list[float]],
    ) -> None:
        _validate_index_payload(document_version_id, chunks, embeddings)
        conn = self._pool.get()
        with conn:
            _validate_persisted_scope(conn, chunks)
            self._validate_embedding_identity(conn, len(embeddings[0]), writing=True)
            _replace_chunks(conn, document_version_id, chunks, embeddings)

    def complete_version_indexing(
        self,
        document_version_id: str,
        chunks: list[EvidenceChunk],
        embeddings: list[list[float]],
        document: Document,
        job: ProcessingJob,
        *,
        expected_job_revision: int,
    ) -> None:
        _validate_index_payload(document_version_id, chunks, embeddings)
        conn = self._pool.get()
        with conn:
            _validate_persisted_scope(conn, chunks)
            self._validate_embedding_identity(conn, len(embeddings[0]), writing=True)
            _replace_chunks(conn, document_version_id, chunks, embeddings)
            _update_document_status(conn, document)
            _update_job_status(
                conn,
                job,
                expected_revision=expected_job_revision,
            )

    def search(
        self,
        *,
        owner_id: str,
        query: str,
        query_embedding: list[float],
        top_k: int = 5,
        document_ids: list[str] | None = None,
    ) -> list[EvidenceSearchHit]:
        if not owner_id or not query.strip() or top_k < 1:
            raise ValueError("owner_id/query 必填且 top_k 必须大于 0")
        sql = """SELECT ec.* FROM chat_evidence_chunks AS ec
                 JOIN chat_documents AS d ON d.document_id = ec.document_id AND d.owner_id = ec.owner_id
                 WHERE ec.owner_id = ? AND d.status = 'ready'
                   AND d.current_version_id = ec.document_version_id"""
        params: list[object] = [owner_id]
        if document_ids is not None:
            if not document_ids:
                return []
            sql += " AND d.document_id IN (" + ",".join("?" for _ in document_ids) + ")"
            params.extend(document_ids)
        rows = self._pool.get().execute(sql, params).fetchall()
        if rows:
            self._validate_embedding_identity(self._pool.get(), len(query_embedding))
        return _rank_rows(rows, query=query, query_embedding=query_embedding, top_k=top_k)

    def count_version(self, document_version_id: str) -> int:
        row = (
            self._pool.get()
            .execute(
                """
            SELECT COUNT(*) AS n FROM chat_evidence_chunks
            WHERE document_version_id = ?
            """,
                (document_version_id,),
            )
            .fetchone()
        )
        return int(row["n"])


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
        raise ValueError("query_embedding 维度与索引不一致")

    vector_ranking = sorted(
        candidates,
        key=lambda candidate: _cosine_similarity(
            query_embedding,
            candidate["embedding"],
        ),
        reverse=True,
    )
    vector_scores = {
        candidate["chunk"].chunk_id: _cosine_similarity(
            query_embedding,
            candidate["embedding"],
        )
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


def _validate_index_payload(
    document_version_id: str,
    chunks: list[EvidenceChunk],
    embeddings: list[list[float]],
) -> None:
    if not chunks:
        raise ValueError("chunks 不能为空")
    if len(chunks) != len(embeddings):
        raise ValueError("chunks 与 embeddings 长度必须一致")
    if any(chunk.document_version_id != document_version_id for chunk in chunks):
        raise ValueError("所有 chunk 必须属于当前 DocumentVersion")
    dimensions = {len(embedding) for embedding in embeddings}
    if 0 in dimensions or len(dimensions) != 1:
        raise ValueError("embedding 维度必须非零且一致")


def _replace_chunks(
    conn: Any,
    document_version_id: str,
    chunks: list[EvidenceChunk],
    embeddings: list[list[float]],
) -> None:
    conn.execute(
        "DELETE FROM chat_evidence_chunks WHERE document_version_id = ?",
        (document_version_id,),
    )
    conn.executemany(
        """
        INSERT INTO chat_evidence_chunks
            (chunk_id, owner_id, document_id, document_version_id,
             page_number, chunk_index, text, source_sha256, embedding_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
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


def _validate_persisted_scope(conn: Any, chunks: list[EvidenceChunk]) -> None:
    for chunk in chunks:
        row = conn.execute(
            """SELECT d.owner_id, dv.document_id, dv.sha256
            FROM chat_document_versions dv JOIN chat_documents d ON d.document_id = dv.document_id
            WHERE dv.version_id = ?""",
            (chunk.document_version_id,),
        ).fetchone()
        if (
            row is None
            or row["document_id"] != chunk.document_id
            or row["owner_id"] != chunk.owner_id
            or row["sha256"] != chunk.source_sha256
        ):
            raise ValueError("EvidenceChunk 与文档归属、版本或 SHA 不一致")


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
    chat_documents = [_tokenize(candidate["chunk"].text) for candidate in candidates]
    if not query_tokens or not chat_documents:
        return {candidate["chunk"].chunk_id: 0.0 for candidate in candidates}
    doc_count = len(chat_documents)
    avg_length = sum(len(tokens) for tokens in chat_documents) / doc_count or 1.0
    document_frequency = Counter(token for tokens in chat_documents for token in set(tokens))
    result: dict[str, float] = {}
    for candidate, tokens in zip(candidates, chat_documents, strict=True):
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


def _update_document_status(conn: Any, document: Document) -> None:
    conn.execute(
        """
        UPDATE chat_documents SET status = ?, updated_at = ?
        WHERE document_id = ?
        """,
        (document.status, document.updated_at, document.document_id),
    )


def _update_job_status(
    conn: Any,
    job: ProcessingJob,
    *,
    expected_revision: int,
) -> None:
    from domain.errors import ProcessingJobConflict

    if job.revision <= expected_revision:
        raise ValueError("ProcessingJob revision 必须递增")
    cursor = conn.execute(
        """
        UPDATE chat_processing_jobs SET
            status = ?, current_stage = ?, progress = ?, error_code = ?,
            error_message = ?, retry_count = ?, updated_at = ?, started_at = ?,
            completed_at = ?, revision = ?
        WHERE job_id = ? AND revision = ?
        """,
        (
            job.status,
            job.current_stage,
            job.progress,
            job.error_code,
            job.error_message,
            job.retry_count,
            job.updated_at,
            job.started_at,
            job.completed_at,
            job.revision,
            job.job_id,
            expected_revision,
        ),
    )
    if cursor.rowcount != 1:
        raise ProcessingJobConflict(job.job_id)
