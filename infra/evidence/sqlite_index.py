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

    def __init__(self, pool: SqliteConnectionPool) -> None:
        self._pool = pool

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
            _replace_chunks(conn, document_version_id, chunks, embeddings)

    def complete_version_indexing(
        self,
        document_version_id: str,
        chunks: list[EvidenceChunk],
        embeddings: list[list[float]],
        document: Document,
        job: ProcessingJob,
    ) -> None:
        _validate_index_payload(document_version_id, chunks, embeddings)
        conn = self._pool.get()
        with conn:
            _validate_persisted_scope(conn, chunks)
            _replace_chunks(conn, document_version_id, chunks, embeddings)
            _update_document_status(conn, document)
            _update_job_status(conn, job)

    def search(
        self,
        *,
        workspace_id: str,
        case_id: str,
        query: str,
        query_embedding: list[float],
        top_k: int = 5,
    ) -> list[EvidenceSearchHit]:
        if not workspace_id or not case_id:
            raise ValueError("workspace_id 和 case_id 必填")
        if not query.strip():
            raise ValueError("query 不能为空")
        if top_k < 1:
            raise ValueError("top_k 必须大于 0")

        rows = (
            self._pool.get()
            .execute(
                """
                SELECT ec.*
                FROM evidence_chunks AS ec
                JOIN documents AS d
                  ON d.document_id = ec.document_id
                 AND d.workspace_id = ec.workspace_id
                JOIN case_documents AS cd
                  ON cd.case_id = ec.case_id
                 AND cd.document_id = ec.document_id
                WHERE ec.workspace_id = ?
                  AND ec.case_id = ?
                  AND d.current_version_id = ec.document_version_id
                """,
                (workspace_id, case_id),
            )
            .fetchall()
        )
        return _rank_rows(
            rows,
            query=query,
            query_embedding=query_embedding,
            top_k=top_k,
        )

    def search_workspace(
        self,
        *,
        workspace_id: str,
        query: str,
        query_embedding: list[float],
        top_k: int = 5,
    ) -> list[EvidenceSearchHit]:
        if not workspace_id:
            raise ValueError("workspace_id 必填")
        if not query.strip():
            raise ValueError("query 不能为空")
        if top_k < 1:
            raise ValueError("top_k 必须大于 0")
        rows = (
            self._pool.get()
            .execute(
                """
                SELECT ec.*
                FROM evidence_chunks AS ec
                JOIN documents AS d ON d.document_id = ec.document_id
                WHERE ec.workspace_id = ?
                  AND d.workspace_id = ?
                  AND d.document_type = 'workspace_knowledge'
                  AND d.status = 'ready'
                  AND d.current_version_id = ec.document_version_id
                GROUP BY
                    ec.document_id,
                    ec.document_version_id,
                    ec.page_number,
                    ec.chunk_index
                """,
                (workspace_id, workspace_id),
            )
            .fetchall()
        )
        return _rank_rows(
            rows,
            query=query,
            query_embedding=query_embedding,
            top_k=top_k,
        )

    def count_version(self, document_version_id: str) -> int:
        row = (
            self._pool.get()
            .execute(
                """
            SELECT COUNT(*) AS n FROM evidence_chunks
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
        "DELETE FROM evidence_chunks WHERE document_version_id = ?",
        (document_version_id,),
    )
    conn.executemany(
        """
        INSERT INTO evidence_chunks
            (chunk_id, workspace_id, case_id, document_id, document_version_id,
             page_number, chunk_index, text, source_sha256, embedding_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                chunk.chunk_id,
                chunk.workspace_id,
                chunk.case_id,
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
            """
            SELECT
                d.workspace_id AS document_workspace_id,
                c.workspace_id AS case_workspace_id,
                dv.document_id AS version_document_id
            FROM document_versions AS dv
            JOIN documents AS d ON d.document_id = dv.document_id
            JOIN compliance_cases AS c ON c.case_id = ?
            JOIN case_documents AS cd
              ON cd.case_id = c.case_id AND cd.document_id = d.document_id
            WHERE dv.version_id = ?
            """,
            (chunk.case_id, chunk.document_version_id),
        ).fetchone()
        if row is None:
            raise ValueError("EvidenceChunk 关联的 Case 或 DocumentVersion 不存在")
        if row["version_document_id"] != chunk.document_id:
            raise ValueError("EvidenceChunk.document_id 与 DocumentVersion 不一致")
        if (
            row["document_workspace_id"] != chunk.workspace_id
            or row["case_workspace_id"] != chunk.workspace_id
        ):
            raise ValueError("EvidenceChunk 的 Workspace 作用域与关联对象不一致")


def _row_to_candidate(row: Any) -> dict[str, Any]:
    return {
        "chunk": EvidenceChunk(
            chunk_id=row["chunk_id"],
            workspace_id=row["workspace_id"],
            case_id=row["case_id"],
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


def _update_document_status(conn: Any, document: Document) -> None:
    conn.execute(
        """
        UPDATE documents SET status = ?, updated_at = ?
        WHERE document_id = ?
        """,
        (document.status, document.updated_at, document.document_id),
    )


def _update_job_status(conn: Any, job: ProcessingJob) -> None:
    conn.execute(
        """
        UPDATE processing_jobs SET
            status = ?, current_stage = ?, progress = ?, error_code = ?,
            error_message = ?, retry_count = ?, updated_at = ?, started_at = ?,
            completed_at = ?
        WHERE job_id = ?
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
            job.job_id,
        ),
    )
