"""IngestionUseCase：通用文档入库入口（占位实现）。

Step 008 阶段只暴露调用骨架：传入 owner_id + 文本列表 → 走 EmbedPort 算 embedding。
真实的"切分 + 落 chroma + 索引 BM25"在后续 PR 接入 ``ingestion/`` 现有管线时再接。

本 use case 故意保持极薄：
- 不做语料路由（law / user_docs）
- 不做去重 / 升级 / merge_owner
- 不持久化 chunks（只返回 embedding 数量便于上层确认链路通）
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from domain.ports import EmbedPort


class IngestionResult(TypedDict):
    owner_id: str
    text_count: int
    embedding_dim: int


class IngestionUseCase:
    def __init__(self, embedder: EmbedPort) -> None:
        self._embedder = embedder

    def ingest_texts(self, owner_id: str, texts: list[str]) -> IngestionResult:
        if not owner_id:
            msg = "owner_id 必填"
            raise ValueError(msg)
        if not texts:
            return {"owner_id": owner_id, "text_count": 0, "embedding_dim": 0}
        # 仅调用 EmbedPort 走通链路；真实分块/落库在 PR-6 接入
        vectors = self._embedder.embed(texts)
        if not vectors:
            return {"owner_id": owner_id, "text_count": len(texts), "embedding_dim": 0}
        return {
            "owner_id": owner_id,
            "text_count": len(texts),
            "embedding_dim": len(vectors[0]),
        }
