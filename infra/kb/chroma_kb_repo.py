"""``KbDocumentRepoPort`` 的 Chroma 实现（Step 016a；Step 025a 多租户扩展）。

包装 ``retrieval/search/vector_store.py:VectorStore`` 的"按 source 聚合"
能力，将其翻译为 domain 层 ``KbDocument`` / ``KbChunk`` 形态，并把
``KbChunk`` 在写入时转换为 infra 层 ``ChunkWithMetadata``。

设计要点：
- 不引入新的 chroma client；复用上层注入的 ``VectorStore`` 实例，保持
  单一 chroma collection 真相源（与现有 ``RetrievePort`` 共享）。
- ``add_chunks`` 在写入前**显式**调用 ``delete_by_source``，与
  ``service.py:_ingest_document`` 行为一致（先删后插的幂等语义）；
  Step 025a 起按 ``(source_name, owner_id)`` 维度幂等。
- 不直接读 ``chromadb`` API；所有交互走 ``VectorStore`` 已有的方法。

Step 025a 多租户：
- ``list_documents`` / ``get_document`` 加 ``owners`` 透传给 vs.get_all_sources
- ``delete_document`` 加 ``owner_id`` 三态：未传 sentinel = admin 不限；
  None = 仅公共；str = 仅该 owner
- ``add_chunks`` 按 ``(source_name, owner_id)`` 维度先删后插；要求同 batch
  同 owner（use case 层负责保证）
- ``_to_kb_document`` 提升 ``owner_id``（PUBLIC_OWNER_MARKER 折回 None）
- ``_to_chunk_with_metadata`` 透传 ``owner_id``
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from domain.models import KbChunk, KbDocument, KbSourceType
from processing.metadata import PUBLIC_OWNER_MARKER, ChunkWithMetadata

if TYPE_CHECKING:
    from retrieval.search.vector_store import VectorStore


class _UnsetType:
    """Step 025a sentinel：区分「未传 owner_id」与「显式传 None=public」。"""

    _instance: _UnsetType | None = None

    def __new__(cls) -> _UnsetType:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "<UNSET>"


_UNSET = _UnsetType()


class ChromaKbRepo:
    """``KbDocumentRepoPort`` 的 Chroma 实现。"""

    def __init__(self, vector_store: VectorStore) -> None:
        self._vs = vector_store

    # ─── 读侧 ────────────────────────────────────────────────────────

    def list_documents(
        self,
        *,
        owners: list[str | None] | None = None,
    ) -> list[KbDocument]:
        raw = self._vs.get_all_sources(owners=owners)
        return [self._to_kb_document(r) for r in raw]

    def get_document(
        self,
        source_name: str,
        *,
        owners: list[str | None] | None = None,
    ) -> KbDocument | None:
        for doc in self.list_documents(owners=owners):
            if doc.source_name == source_name:
                return doc
        return None

    def count_chunks(self) -> int:
        return self._vs.get_total_count()

    # ─── 写侧 ────────────────────────────────────────────────────────

    def delete_document(
        self,
        source_name: str,
        *,
        owner_id: Any = _UNSET,
    ) -> int:
        """删除文档（按 source_name + 可选 owner_id 过滤）。

        ``owner_id``：
        - 未传（``_UNSET``）→ 不限 owner（admin 全能删，含公共与他人）
        - ``None`` → 仅删公共
        - ``str`` → 仅删该 owner 的
        """
        if not source_name:
            msg = "source_name 不能为空"
            raise ValueError(msg)
        if isinstance(owner_id, _UnsetType):
            return self._vs.delete_by_source(source_name)
        return self._vs.delete_by_source(source_name, owner_id=owner_id)

    def add_chunks(
        self,
        chunks: list[KbChunk],
        embeddings: list[list[float]],
    ) -> None:
        """同 ``(source_name, owner_id)`` 维度幂等：先删该维度再批量插。

        约定：同一次调用的 chunks 必须同源同 owner（use case 层负责），否则按
        每个唯一 ``(source_name, owner_id)`` 拆 delete 即可。
        """
        if len(chunks) != len(embeddings):
            msg = (
                f"chunks 与 embeddings 长度必须一致："
                f"len(chunks)={len(chunks)}, len(embeddings)={len(embeddings)}"
            )
            raise ValueError(msg)
        if not chunks:
            return

        # 先删后插：按 (source_name, owner_id) 维度替换（同名跨 owner 互不干扰）
        keys: set[tuple[str, str | None]] = {
            (c.source_name, c.owner_id) for c in chunks
        }
        for src, owner in keys:
            self._vs.delete_by_source(src, owner_id=owner)

        cwm_list = [self._to_chunk_with_metadata(c) for c in chunks]
        self._vs.add_chunks(cwm_list, embeddings)

    # ─── 启动迁移（Step 025a） ─────────────────────────────────────────

    def migrate_owner_id_legacy(self) -> int:
        """Step 025a 启动懒迁移：把缺 owner_id 的旧 chunk 标记为公共库。

        透传到 ``VectorStore.migrate_owner_id_marker``；幂等。
        """
        return self._vs.migrate_owner_id_marker()

    # ─── 转换 ────────────────────────────────────────────────────────

    @staticmethod
    def _to_kb_document(raw: dict[str, object]) -> KbDocument:
        """``VectorStore.get_all_sources`` 行 → ``KbDocument``。

        老 vector_store 的 ``source_type`` 字段值是字符串（含 ``"unknown"`` 兜底）；
        本方法把不属于 ``file``/``web`` 的值归一为 ``"file"``，避免 domain 模型校验失败。
        """
        raw_type = str(raw.get("source_type", "file"))
        src_type: KbSourceType = "web" if raw_type == "web" else "file"
        raw_owner = raw.get("owner_id")
        owner_id: str | None = (
            None
            if raw_owner in (None, "", PUBLIC_OWNER_MARKER)
            else str(raw_owner)
        )
        return KbDocument(
            source_name=str(raw.get("source_name") or "unknown"),
            source_type=src_type,
            title=str(raw.get("title") or ""),
            source_url=cast("str | None", raw.get("source_url") or None),
            chunk_count=int(cast("int", raw.get("chunk_count", 0))),
            category=str(raw.get("category") or ""),
            owner_id=owner_id,
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
            owner_id=c.owner_id,
        )
