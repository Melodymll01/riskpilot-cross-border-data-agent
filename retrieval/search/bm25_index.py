"""BM25 索引：从 ChromaDB 懒加载全量语料，内存中维护 BM25Okapi。

设计原则：
- 懒加载：首次调用 search() 时才从 Chroma 拉全量并建索引
- 脏标记：VectorStore 在 add/delete 时调用 mark_dirty() 触发重建
- 线程不安全：当前单进程单索引，够用

分词：中文用 jieba.cut_for_search（更细粒度，利于短查询命中），
      英文/数字天然按字符被 jieba 处理。

Step 025a 多租户：``search`` 加 ``viewers`` 参数，召回后按 metadata.owner_id 过滤。
不为 BM25 维护 per-owner 索引（开销不值）；全局索引 + 召回过滤足够。
"""

import logging
import threading
from collections.abc import Iterable
from typing import Any

from processing.metadata import PUBLIC_OWNER_MARKER

logger = logging.getLogger(__name__)


def _tokenize(text: str) -> list[str]:
    """中英混合分词：jieba 搜索模式 + 小写化。"""
    import jieba
    if not text:
        return []
    tokens = [t.strip().lower() for t in jieba.cut_for_search(text)]
    return [t for t in tokens if t and not t.isspace()]


def _owner_matches(meta_owner: Any, viewer_set: set) -> bool:
    """判断单条 chunk 的 metadata.owner_id 是否落在 viewer_set 内。

    存储侧把 ``None`` 物化为 ``PUBLIC_OWNER_MARKER``，
    domain 侧 ``None`` 表示公共；本函数统一在比较前折叠。
    """
    raw = meta_owner if meta_owner not in ("", None) else None
    normalized = None if raw == PUBLIC_OWNER_MARKER else raw
    return normalized in viewer_set


class BM25Index:
    """基于 rank_bm25 的内存索引，延迟构建、支持失效重建。"""

    def __init__(self, vector_store):
        self.vector_store = vector_store
        self._bm25 = None
        self._doc_ids: list[str] = []
        self._docs: list[dict[str, Any]] = []
        self._dirty = True
        self._lock = threading.Lock()

    def mark_dirty(self) -> None:
        """标记索引失效。下次 search 会重建。"""
        with self._lock:
            self._dirty = True

    def _build(self) -> None:
        """从 Chroma 拉全量文档，构建 BM25 索引。"""
        from rank_bm25 import BM25Okapi

        all_data = self.vector_store.collection.get(
            include=["documents", "metadatas"],
        )
        ids = all_data.get("ids") or []
        docs = all_data.get("documents") or []
        metas = all_data.get("metadatas") or []

        if not ids:
            self._bm25 = None
            self._doc_ids = []
            self._docs = []
            self._dirty = False
            logger.info("BM25 索引为空（向量库无数据）")
            return

        tokenized_corpus = [_tokenize(d or "") for d in docs]
        # rank_bm25 默认 k1=1.5, b=0.75
        self._bm25 = BM25Okapi(tokenized_corpus)
        self._doc_ids = list(ids)
        self._docs = [
            {"id": ids[i], "text": docs[i], "metadata": metas[i] if i < len(metas) else {}}
            for i in range(len(ids))
        ]
        self._dirty = False
        logger.info(f"BM25 索引已构建：{len(ids)} 个文档")

    def _ensure_ready(self) -> None:
        with self._lock:
            if self._dirty or self._bm25 is None:
                self._build()

    def search(
        self,
        query: str,
        top_k: int = 10,
        viewers: Iterable[str | None] | None = None,
    ) -> list[dict[str, Any]]:
        """返回按 BM25 分数降序排列的文档（含 rank 和 bm25_score）。

        Step 025a：``viewers`` 可见 owner_id 集合（含 ``None`` 表示公共）；
        ``None`` 不过滤。多召回 + 后置过滤策略：先取 ``top_k * 4`` 候选，
        过滤后再截到 ``top_k``，避免 owner 过滤把前排好结果全清空。
        """
        self._ensure_ready()
        if self._bm25 is None or not self._doc_ids:
            return []

        tokens = _tokenize(query)
        if not tokens:
            return []

        scores = self._bm25.get_scores(tokens)
        viewer_set: set | None = None
        if viewers is not None:
            viewer_set = set(viewers)
            if not viewer_set:
                return []  # 空集 == 谁都看不到

        # 取候选：viewers 不为 None 时多取一些以缓冲过滤损耗
        candidate_n = top_k * 4 if viewer_set is not None else top_k
        indexed = [(i, float(scores[i])) for i in range(len(scores)) if scores[i] > 0]
        indexed.sort(key=lambda x: x[1], reverse=True)
        indexed = indexed[:candidate_n]

        results = []
        for rank, (idx, score) in enumerate(indexed, start=1):
            doc = self._docs[idx]
            if viewer_set is not None and not _owner_matches(
                doc["metadata"].get("owner_id"), viewer_set
            ):
                continue
            results.append({
                "id": doc["id"],
                "text": doc["text"],
                "metadata": doc["metadata"],
                "bm25_score": score,
                "bm25_rank": rank,
                "match_type": "bm25",
            })
            if len(results) >= top_k:
                break
        return results
