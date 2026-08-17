"""SQLAlchemy EvidenceIndexPort 过渡实现。

Phase 2 先保证 PostgreSQL profile 的作用域、事务和完整闭环；Phase 3 将 embedding
列升级为 pgvector，并把 dense/FTS 候选查询下推数据库。
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

from sqlalchemy import delete, select

from domain.documents import Document, ProcessingJob
from domain.evidence import EvidenceChunk, EvidenceSearchHit
from infra.storage.sqlalchemy.database import SqlAlchemyDatabase
from infra.storage.sqlalchemy.document_repo import _apply_document, _apply_job
from infra.storage.sqlalchemy.mapping import require_datetime, require_timestamp
from infra.storage.sqlalchemy.models import (
    CaseDocumentRow,
    CaseRow,
    DocumentRow,
    DocumentVersionRow,
    EvidenceChunkRow,
    ProcessingJobRow,
)

_RRF_K = 60


class SqlAlchemyEvidenceIndex:
    def __init__(self, database: SqlAlchemyDatabase) -> None:
        self._database = database

    def replace_version_chunks(
        self,
        document_version_id: str,
        chunks: list[EvidenceChunk],
        embeddings: list[list[float]],
    ) -> None:
        _validate_payload(document_version_id, chunks, embeddings)
        with self._database.session() as session:
            _validate_scope(session, chunks)
            session.execute(
                delete(EvidenceChunkRow).where(
                    EvidenceChunkRow.document_version_id == document_version_id
                )
            )
            session.add_all(
                _row(chunk, embedding) for chunk, embedding in zip(chunks, embeddings, strict=True)
            )

    def complete_version_indexing(
        self,
        document_version_id: str,
        chunks: list[EvidenceChunk],
        embeddings: list[list[float]],
        document: Document,
        job: ProcessingJob,
    ) -> None:
        _validate_payload(document_version_id, chunks, embeddings)
        with self._database.session() as session:
            _validate_scope(session, chunks)
            session.execute(
                delete(EvidenceChunkRow).where(
                    EvidenceChunkRow.document_version_id == document_version_id
                )
            )
            session.add_all(
                _row(chunk, embedding) for chunk, embedding in zip(chunks, embeddings, strict=True)
            )
            document_row = session.get(DocumentRow, document.document_id)
            job_row = session.get(ProcessingJobRow, job.job_id)
            if document_row is None or job_row is None:
                raise ValueError("Document 或 ProcessingJob 不存在")
            _apply_document(document_row, document)
            _apply_job(job_row, job)

    def search(
        self,
        *,
        workspace_id: str,
        case_id: str,
        query: str,
        query_embedding: list[float],
        top_k: int = 5,
    ) -> list[EvidenceSearchHit]:
        _validate_search(workspace_id, query, top_k, case_id=case_id)
        statement = select(EvidenceChunkRow).where(
            EvidenceChunkRow.workspace_id == workspace_id,
            EvidenceChunkRow.case_id == case_id,
        )
        with self._database.read_session() as session:
            rows = list(session.scalars(statement))
        return _rank(rows, query=query, query_embedding=query_embedding, top_k=top_k)

    def search_workspace(
        self,
        *,
        workspace_id: str,
        query: str,
        query_embedding: list[float],
        top_k: int = 5,
    ) -> list[EvidenceSearchHit]:
        _validate_search(workspace_id, query, top_k)
        statement = (
            select(EvidenceChunkRow)
            .join(
                DocumentRow,
                DocumentRow.document_id == EvidenceChunkRow.document_id,
            )
            .where(
                EvidenceChunkRow.workspace_id == workspace_id,
                DocumentRow.workspace_id == workspace_id,
                DocumentRow.document_type == "workspace_knowledge",
                DocumentRow.status == "ready",
                DocumentRow.current_version_id == EvidenceChunkRow.document_version_id,
            )
        )
        with self._database.read_session() as session:
            rows = list(session.scalars(statement))
        unique = {
            (
                row.document_id,
                row.document_version_id,
                row.page_number,
                row.chunk_index,
            ): row
            for row in rows
        }
        return _rank(
            list(unique.values()),
            query=query,
            query_embedding=query_embedding,
            top_k=top_k,
        )

    def count_version(self, document_version_id: str) -> int:
        statement = select(EvidenceChunkRow.chunk_id).where(
            EvidenceChunkRow.document_version_id == document_version_id
        )
        with self._database.read_session() as session:
            return len(list(session.scalars(statement)))


def _validate_payload(
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


def _validate_scope(session: Any, chunks: list[EvidenceChunk]) -> None:
    for chunk in chunks:
        statement = (
            select(
                DocumentRow.workspace_id,
                CaseRow.workspace_id,
                DocumentVersionRow.document_id,
            )
            .join(
                DocumentRow,
                DocumentRow.document_id == DocumentVersionRow.document_id,
            )
            .join(CaseRow, CaseRow.case_id == chunk.case_id)
            .join(
                CaseDocumentRow,
                (CaseDocumentRow.case_id == CaseRow.case_id)
                & (CaseDocumentRow.document_id == DocumentRow.document_id),
            )
            .where(DocumentVersionRow.version_id == chunk.document_version_id)
        )
        row = session.execute(statement).one_or_none()
        if row is None:
            raise ValueError("EvidenceChunk 关联的 Case 或 DocumentVersion 不存在")
        document_workspace_id, case_workspace_id, document_id = row
        if document_id != chunk.document_id:
            raise ValueError("EvidenceChunk.document_id 与 DocumentVersion 不一致")
        if document_workspace_id != chunk.workspace_id or case_workspace_id != chunk.workspace_id:
            raise ValueError("EvidenceChunk 的 Workspace 作用域与关联对象不一致")


def _validate_search(
    workspace_id: str,
    query: str,
    top_k: int,
    *,
    case_id: str | None = None,
) -> None:
    if not workspace_id or case_id == "":
        raise ValueError("workspace_id 和 case_id 必填")
    if not query.strip():
        raise ValueError("query 不能为空")
    if top_k < 1:
        raise ValueError("top_k 必须大于 0")


def _row(chunk: EvidenceChunk, embedding: list[float]) -> EvidenceChunkRow:
    return EvidenceChunkRow(
        chunk_id=chunk.chunk_id,
        workspace_id=chunk.workspace_id,
        case_id=chunk.case_id,
        document_id=chunk.document_id,
        document_version_id=chunk.document_version_id,
        page_number=chunk.page_number,
        chunk_index=chunk.chunk_index,
        text=chunk.text,
        source_sha256=chunk.source_sha256,
        embedding=embedding,
        created_at=require_datetime(chunk.created_at),
    )


def _chunk(row: EvidenceChunkRow) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=row.chunk_id,
        workspace_id=row.workspace_id,
        case_id=row.case_id,
        document_id=row.document_id,
        document_version_id=row.document_version_id,
        page_number=row.page_number,
        chunk_index=row.chunk_index,
        text=row.text,
        source_sha256=row.source_sha256,
        created_at=require_timestamp(row.created_at),
    )


def _rank(
    rows: list[EvidenceChunkRow],
    *,
    query: str,
    query_embedding: list[float],
    top_k: int,
) -> list[EvidenceSearchHit]:
    if any(len(row.embedding) != len(query_embedding) for row in rows):
        raise ValueError("query_embedding 维度与索引不一致")
    vector_scores = {row.chunk_id: _cosine(query_embedding, row.embedding) for row in rows}
    vector_ranking = sorted(
        rows,
        key=lambda row: vector_scores[row.chunk_id],
        reverse=True,
    )
    bm25_scores = _bm25(query, rows)
    bm25_ranking = sorted(
        rows,
        key=lambda row: bm25_scores[row.chunk_id],
        reverse=True,
    )
    vector_ranks = {row.chunk_id: rank for rank, row in enumerate(vector_ranking, start=1)}
    bm25_ranks = {
        row.chunk_id: rank
        for rank, row in enumerate(bm25_ranking, start=1)
        if bm25_scores[row.chunk_id] > 0
    }
    hits = []
    for row in rows:
        score = 1.0 / (_RRF_K + vector_ranks[row.chunk_id])
        if row.chunk_id in bm25_ranks:
            score += 1.0 / (_RRF_K + bm25_ranks[row.chunk_id])
        hits.append(
            EvidenceSearchHit(
                chunk=_chunk(row),
                score=score,
                vector_score=vector_scores[row.chunk_id],
                bm25_score=bm25_scores[row.chunk_id],
            )
        )
    hits.sort(key=lambda hit: (hit.score, hit.vector_score), reverse=True)
    return hits[:top_k]


def _cosine(left: list[float], right: list[float]) -> float:
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    score = sum(a * b for a, b in zip(left, right, strict=True))
    return max(-1.0, min(1.0, score / (left_norm * right_norm)))


def _bm25(query: str, rows: list[EvidenceChunkRow]) -> dict[str, float]:
    query_tokens = _tokenize(query)
    documents = [_tokenize(row.text) for row in rows]
    if not query_tokens or not documents:
        return {row.chunk_id: 0.0 for row in rows}
    doc_count = len(documents)
    avg_length = sum(len(tokens) for tokens in documents) / doc_count or 1.0
    document_frequency = Counter(token for tokens in documents for token in set(tokens))
    result: dict[str, float] = {}
    for row, tokens in zip(rows, documents, strict=True):
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
        result[row.chunk_id] = score
    return result


def _tokenize(text: str) -> list[str]:
    import jieba

    return [
        token for token in (part.strip().lower() for part in jieba.cut_for_search(text)) if token
    ]
