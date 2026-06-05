"""``KbDocumentRepoPort`` Fake：in-memory 字典存储。

设计目标：
- 满足 ``KbDocumentRepoPort`` Protocol 的 isinstance 契约检查
- 暴露 ``calls`` / ``written_chunks`` 便于测试断言副作用
- 行为与 ``ChromaKbRepo`` 语义对齐："先删后插"幂等
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy

from domain.models import KbChunk, KbDocument, KbSourceType


class FakeKbRepo:
    """in-memory ``KbDocumentRepoPort`` 实现。"""

    def __init__(self) -> None:
        # source_name -> list[KbChunk]
        self._store: dict[str, list[KbChunk]] = defaultdict(list)
        # 记录每次 add_chunks 的形态，方便测试断言
        self.written_chunks: list[KbChunk] = []
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    # ─── 读侧 ────────────────────────────────────────────────────────

    def list_documents(self) -> list[KbDocument]:
        self.calls.append(("list_documents", ()))
        out: list[KbDocument] = []
        for src, chunks in self._store.items():
            if not chunks:
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
                )
            )
        return out

    def get_document(self, source_name: str) -> KbDocument | None:
        self.calls.append(("get_document", (source_name,)))
        for doc in self.list_documents():
            if doc.source_name == source_name:
                return doc
        return None

    def count_chunks(self) -> int:
        self.calls.append(("count_chunks", ()))
        return sum(len(v) for v in self._store.values())

    # ─── 写侧 ────────────────────────────────────────────────────────

    def delete_document(self, source_name: str) -> int:
        self.calls.append(("delete_document", (source_name,)))
        if source_name not in self._store:
            return 0
        n = len(self._store[source_name])
        del self._store[source_name]
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
        # 先删后插：同 source 旧数据替换
        for src in {c.source_name for c in chunks}:
            self._store.pop(src, None)
        for c in chunks:
            self._store[c.source_name].append(c)
            self.written_chunks.append(deepcopy(c))
