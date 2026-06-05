"""``KbDocumentRepoPort`` 的 Chroma 实现（Step 016a）。

包装 ``retrieval/search/vector_store.py:VectorStore`` 的"按 source 聚合"
能力，将其翻译为 domain 层 ``KbDocument`` / ``KbChunk`` 形态，并把
``KbChunk`` 在写入时转换为 infra 层 ``ChunkWithMetadata``。

设计要点：
- 不引入新的 chroma client；复用上层注入的 ``VectorStore`` 实例，保持
  单一 chroma collection 真相源（与现有 ``RetrievePort`` 共享）。
- ``add_chunks`` 在写入前**显式**调用 ``delete_by_source``，与
  ``service.py:_ingest_document`` 行为一致（先删后插的幂等语义）。
- 不直接读 ``chromadb`` API；所有交互走 ``VectorStore`` 已有的方法。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from domain.models import KbChunk, KbDocument, KbSourceType
from processing.metadata import ChunkWithMetadata

if TYPE_CHECKING:
    from retrieval.search.vector_store import VectorStore


class ChromaKbRepo:
    """``KbDocumentRepoPort`` 的 Chroma 实现。"""

    def __init__(self, vector_store: VectorStore) -> None:
        self._vs = vector_store

    # ─── 读侧 ────────────────────────────────────────────────────────

    def list_documents(self) -> list[KbDocument]:
        raw = self._vs.get_all_sources()
        return [self._to_kb_document(r) for r in raw]

    def get_document(self, source_name: str) -> KbDocument | None:
        for doc in self.list_documents():
            if doc.source_name == source_name:
                return doc
        return None

    def count_chunks(self) -> int:
        return self._vs.get_total_count()

    # ─── 写侧 ────────────────────────────────────────────────────────

    def delete_document(self, source_name: str) -> int:
        if not source_name:
            msg = "source_name 不能为空"
            raise ValueError(msg)
        return self._vs.delete_by_source(source_name)

    def add_chunks(
        self,
        chunks: list[KbChunk],
        embeddings: list[list[float]],
    ) -> None:
        if len(chunks) != len(embeddings):
            msg = (
                f"chunks 与 embeddings 长度必须一致："
                f"len(chunks)={len(chunks)}, len(embeddings)={len(embeddings)}"
            )
            raise ValueError(msg)
        if not chunks:
            return

        # 先删后插（与 service.py:_ingest_document 行为一致）
        sources = {c.source_name for c in chunks}
        for src in sources:
            self._vs.delete_by_source(src)

        cwm_list = [self._to_chunk_with_metadata(c) for c in chunks]
        self._vs.add_chunks(cwm_list, embeddings)

    # ─── 转换 ────────────────────────────────────────────────────────

    @staticmethod
    def _to_kb_document(raw: dict[str, object]) -> KbDocument:
        """``VectorStore.get_all_sources`` 行 → ``KbDocument``。

        老 vector_store 的 ``source_type`` 字段值是字符串（含 ``"unknown"`` 兜底）；
        本方法把不属于 ``file``/``web`` 的值归一为 ``"file"``，避免 domain 模型校验失败。
        """
        raw_type = str(raw.get("source_type", "file"))
        src_type: KbSourceType = "web" if raw_type == "web" else "file"
        return KbDocument(
            source_name=str(raw.get("source_name") or "unknown"),
            source_type=src_type,
            title=str(raw.get("title") or ""),
            source_url=cast("str | None", raw.get("source_url") or None),
            chunk_count=int(cast("int", raw.get("chunk_count", 0))),
            category=str(raw.get("category") or ""),
        )

    @staticmethod
    def _to_chunk_with_metadata(c: KbChunk) -> ChunkWithMetadata:
        return ChunkWithMetadata(
            chunk_id=c.chunk_id,
            text=c.text,
            source_type=c.source_type,
            source_name=c.source_name,
            title=c.title,
            source_url=c.source_url,
            chunk_index=c.chunk_index,
            category=c.category,
        )
