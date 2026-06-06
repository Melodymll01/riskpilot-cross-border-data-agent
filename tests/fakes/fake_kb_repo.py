"""``KbDocumentRepoPort`` Fake：in-memory 字典存储。

设计目标：
- 满足 ``KbDocumentRepoPort`` Protocol 的 isinstance 契约检查
- 暴露 ``calls`` / ``written_chunks`` 便于测试断言副作用
- 行为与 ``ChromaKbRepo`` 语义对齐："先删后插"幂等
- Step 025a 后支持 ``owners`` 过滤与 owner 维度的 ``delete_document``
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy

from domain.models import KbChunk, KbDocument, KbSourceType


class _UnsetType:
    """sentinel：区分「未传 owner_id」与「显式传 None=public」。"""

    _instance: _UnsetType | None = None

    def __new__(cls) -> _UnsetType:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "<UNSET>"


_UNSET = _UnsetType()


class FakeKbRepo:
    """in-memory ``KbDocumentRepoPort`` 实现。"""

    def __init__(self) -> None:
        # (source_name, owner_id) -> list[KbChunk]，以避免同名跨 owner 文档误聚合
        self._store: dict[tuple[str, str | None], list[KbChunk]] = defaultdict(list)
        # 记录每次 add_chunks 的形态，方便测试断言
        self.written_chunks: list[KbChunk] = []
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    # ─── 读侧 ────────────────────────────────────────────────────────

    def list_documents(
        self,
        *,
        owners: list[str | None] | None = None,
    ) -> list[KbDocument]:
        self.calls.append(("list_documents", (owners,)))
        owner_set: set[str | None] | None = None
        if owners is not None:
            owner_set = set(owners)
        out: list[KbDocument] = []
        for (src, owner), chunks in self._store.items():
            if not chunks:
                continue
            if owner_set is not None and owner not in owner_set:
                continue
            head = chunks[0]
            src_type: KbSourceType = "web" if head.source_type == "web" else "file"
            out.append(
                KbDocument(
                    source_name=src,
                    source_type=src_type,
                    title=head.title,
                    source_url=head.source_url,
                    chunk_count=len(chunks),
                    category=head.category,
                    owner_id=owner,
                )
            )
        return out

    def get_document(
        self,
        source_name: str,
        *,
        owners: list[str | None] | None = None,
    ) -> KbDocument | None:
        self.calls.append(("get_document", (source_name, owners)))
        for doc in self.list_documents(owners=owners):
            if doc.source_name == source_name:
                return doc
        return None

    def count_chunks(self) -> int:
        self.calls.append(("count_chunks", ()))
        return sum(len(v) for v in self._store.values())

    # ─── 写侧 ────────────────────────────────────────────────────────

    def delete_document(
        self,
        source_name: str,
        *,
        owner_id: str | None | _UnsetType = _UNSET,
    ) -> int:
        self.calls.append(("delete_document", (source_name, owner_id)))
        targets = [
            key
            for key in self._store
            if key[0] == source_name
            and (isinstance(owner_id, _UnsetType) or key[1] == owner_id)
        ]
        n = sum(len(self._store[k]) for k in targets)
        for k in targets:
            del self._store[k]
        return n

    def add_chunks(
        self,
        chunks: list[KbChunk],
        embeddings: list[list[float]],
    ) -> None:
        self.calls.append(("add_chunks", (len(chunks), len(embeddings))))
        if len(chunks) != len(embeddings):
            msg = (
                f"chunks 与 embeddings 长度必须一致："
                f"len(chunks)={len(chunks)}, len(embeddings)={len(embeddings)}"
            )
            raise ValueError(msg)
        if not chunks:
            return
        # 先删后插：按 (source_name, owner_id) 维度替换
        for c in chunks:
            self._store.pop((c.source_name, c.owner_id), None)
        for c in chunks:
            self._store[(c.source_name, c.owner_id)].append(c)
            self.written_chunks.append(deepcopy(c))
