"""BM25 索引：从 ChromaDB 懒加载全量语料，内存中维护 BM25Okapi。

设计原则：
- 懒加载：首次调用 search() 时才从 Chroma 拉全量并建索引
- 脏标记：VectorStore 在 add/delete 时调用 mark_dirty() 触发重建
- 线程不安全：当前单进程单索引，够用

分词：中文用 jieba.cut_for_search（更细粒度，利于短查询命中），
      英文/数字天然按字符被 jieba 处理。
"""

import logging
import threading
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


def _tokenize(text: str) -> List[str]:
    """中英混合分词：jieba 搜索模式 + 小写化。"""
    import jieba
    if not text:
        return []
    tokens = [t.strip().lower() for t in jieba.cut_for_search(text)]
    return [t for t in tokens if t and not t.isspace()]


class BM25Index:
    """基于 rank_bm25 的内存索引，延迟构建、支持失效重建。"""

    def __init__(self, vector_store):
        self.vector_store = vector_store
        self._bm25 = None
        self._doc_ids: List[str] = []
        self._docs: List[Dict[str, Any]] = []
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

    def search(self, query: str, top_k: int = 10) -> List[Dict[str, Any]]:
        """返回按 BM25 分数降序排列的文档（含 rank 和 bm25_score）。"""
        self._ensure_ready()
        if self._bm25 is None or not self._doc_ids:
            return []

        tokens = _tokenize(query)
        if not tokens:
            return []

        scores = self._bm25.get_scores(tokens)
        # 取 top_k，过滤零分
        indexed = [(i, float(scores[i])) for i in range(len(scores)) if scores[i] > 0]
        indexed.sort(key=lambda x: x[1], reverse=True)
        indexed = indexed[:top_k]

        results = []
        for rank, (idx, score) in enumerate(indexed, start=1):
            doc = self._docs[idx]
            results.append({
                "id": doc["id"],
                "text": doc["text"],
                "metadata": doc["metadata"],
                "bm25_score": score,
                "bm25_rank": rank,
                "match_type": "bm25",
            })
        return results
