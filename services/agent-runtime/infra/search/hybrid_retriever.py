"""`RetrievePort` 适配器：包装现有 `retrieval.search.retriever.Retriever`。

完成两件事：
1. 把已经组合好的 BM25 + 向量 + RRF + Reranker 流水线统一暴露为单一入口。
2. 把旧 `dict` 形式的检索结果转换为 domain `Chunk`（统一方向：score 越大越相关）。

Step 025a 多租户：``owner_id`` 翻译为 ``viewers``：
- ``owner_id=None``（匿名）→ ``viewers=[None]``，仅看公共
不引入 ``viewer_is_admin``：admin 检索默认也走"公共∪自己"视角，避免越权拼装。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, Protocol

from domain.models import Chunk, Corpus
from processing.metadata import PUBLIC_OWNER_MARKER

if TYPE_CHECKING:
    from config import Settings
    from domain.ports import RunControlPort

logger = logging.getLogger(__name__)


class _RetrieverLike(Protocol):
    def retrieve(
        self,
        query: str,
        top_k: int = ...,
        viewers: Iterable[str | None] | None = ...,
        runtime_control: RunControlPort | None = ...,
    ) -> list[dict[str, Any]]: ...


class HybridRetrieverAdapter:
    """实现 `RetrievePort`，委托给现有 `Retriever`。"""

    def __init__(
        self, retriever: _RetrieverLike | None = None, *, model_settings: Settings | None = None
    ) -> None:
        # retriever=None → 懒构造：首次 retrieve 时才 new Retriever 并注入
        # build_reranker()。CrossEncoder 精排会同步加载 ~1GB 模型，绝不能在
        # 容器构造 / app import 时加载（否则 `from main import app` 阻塞）。
        self._retriever = retriever
        self._model_settings = model_settings

    def _ensure_retriever(self) -> _RetrieverLike:
        if self._retriever is None:
            from retrieval.search.embedder import Embedder
            from retrieval.search.query_rewriter import QueryRewriter
            from retrieval.search.reranker import build_reranker
            from retrieval.search.retriever import Retriever
            from retrieval.search.vector_store import VectorStore

            self._retriever = Retriever(
                embedder=Embedder(config=self._model_settings),
                vector_store=VectorStore(config=self._model_settings),
                reranker=build_reranker(config=self._model_settings),
                query_rewriter=QueryRewriter(config=self._model_settings),
                config=self._model_settings,
            )
        return self._retriever

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        corpus: Corpus = "law",
        owner_id: str | None = None,
        runtime_control: RunControlPort | None = None,
    ) -> list[Chunk]:
        if corpus == "law":
            viewers: list[str | None] = [None]
        elif corpus == "user_docs" and owner_id:
            viewers = [owner_id]
        else:
            raise ValueError("user_docs 检索必须提供 owner_id")
        retriever = self._ensure_retriever()
        if runtime_control is None:
            raw = retriever.retrieve(query, top_k=top_k, viewers=viewers)
        else:
            raw = retriever.retrieve(
                query,
                top_k=top_k,
                viewers=viewers,
                runtime_control=runtime_control,
            )
        return [_dict_to_chunk(item) for item in raw]


def _dict_to_chunk(item: dict[str, Any]) -> Chunk:
    """旧 dict → domain Chunk。

    score 方向统一：取已有的 rerank_score / rrf_score；若只有 distance，按 (1 - distance) 翻转。
    metadata 中的 source_* 字段提升为 Chunk 顶层字段，剩余键保留在 metadata 内。
    """
    meta_in = dict(item.get("metadata") or {})

    # 顶层字段
    chunk_id = item.get("id") or meta_in.get("chunk_id") or "unknown"
    text = item.get("text") or item.get("original_text") or ""
    source_type = meta_in.get("source_type") or "law"
    source_name = meta_in.get("source_name") or "unknown"
    title = meta_in.get("title") or ""
    source_url = meta_in.get("source_url") or None
    category = meta_in.get("category") or ""
    # Step 025a：owner_id 从 metadata 提升；PUBLIC_OWNER_MARKER 折回 None
    raw_owner = meta_in.get("owner_id")
    owner_id_norm: str | None = (
        None if raw_owner in ("", None, PUBLIC_OWNER_MARKER) else str(raw_owner)
    )

    # score：优先 rerank_score → rrf_score → 1-distance
    if "rerank_score" in item:
        score = float(item["rerank_score"])
    elif "rrf_score" in item:
        score = float(item["rrf_score"])
    elif "distance" in item and item["distance"] is not None:
        score = float(1.0 - float(item["distance"]))
    else:
        score = 0.0

    # 剩余 metadata：清理已提升到顶层的字段
    metadata_out = {
        k: v
        for k, v in meta_in.items()
        if k
        not in {
            "source_type",
            "source_name",
            "title",
            "source_url",
            "category",
            "owner_id",
        }
    }
    # 保留检索辅助字段（chunk_index / context_expanded / fused_from / match_type 等）
    for extra_key in ("match_type", "fused_from", "bm25_score", "bm25_rank"):
        if extra_key in item:
            metadata_out[extra_key] = item[extra_key]

    return Chunk(
        chunk_id=str(chunk_id),
        text=str(text),
        source_type=str(source_type),
        source_name=str(source_name),
        title=str(title),
        source_url=source_url,
        category=str(category),
        owner_id=owner_id_norm,
        score=score,
        metadata=metadata_out,
    )
