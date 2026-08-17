"""检索模块：组合 QueryRewriter + Embedder + VectorStore + Reranker 完成端到端检索。

包含关键 RAG 优化：
1. 多查询检索（Multi-Query）：对改写后的多条查询分别检索，合并结果
2. 混合检索（Hybrid Search）：向量语义检索 + BM25 关键词检索，RRF 融合排名
3. 上下文窗口扩展（Context Window）：基于 chunk_index 拉取相邻 chunk 补充上下文
4. 结果去重：基于中文 bigram 的文本去重，消除 overlap 和多查询带来的重复
"""

import logging
import re
from collections.abc import Iterable
from typing import Any

from config import settings
from retrieval.search.bm25_index import BM25Index
from retrieval.search.embedder import Embedder
from retrieval.search.fusion import rrf_fuse
from retrieval.search.query_rewriter import QueryRewriter
from retrieval.search.reranker import BaseReranker, DistanceThresholdReranker
from retrieval.search.vector_store import VectorStore

logger = logging.getLogger(__name__)


class Retriever:
    """
    检索器：串联查询改写 → 向量化 → 多查询检索 → 去重 → 上下文扩展 → 重排序。
    """

    def __init__(
        self,
        embedder: Embedder,
        vector_store: VectorStore,
        reranker: BaseReranker | None = None,
        query_rewriter: QueryRewriter | None = None,
    ):
        self.embedder = embedder
        self.vector_store = vector_store
        self.reranker = reranker or DistanceThresholdReranker()
        self.query_rewriter = query_rewriter or QueryRewriter()
        # BM25 索引：懒加载，向量库数据变动时自动失效重建
        self.bm25_index = BM25Index(vector_store) if settings.enable_bm25_rrf else None
        if self.bm25_index is not None:
            self.vector_store.register_change_listener(self.bm25_index.mark_dirty)

    def retrieve(
        self,
        query: str,
        top_k: int = settings.top_k,
        viewers: Iterable[str | None] | None = None,
    ) -> list[dict[str, Any]]:
        """
        根据用户问题检索最相关的 chunk。

        流程:
        1. 查询改写 → 生成 1~3 条检索查询
        2. 多查询检索 → 分别向量化并在向量库中检索
        3. 合并去重 → 消除多查询/overlap 带来的重复内容
        4. 重排序 → 过滤低相关结果
        5. 上下文扩展 → 为每个 chunk 补充相邻 chunk 的内容

        Step 025a：``viewers`` = 可见 owner_id 集合（含 ``None`` 表示公共）；
        ``None`` = 不限（admin 全库视角）。该参数透传给所有 vector / BM25 调用。
        """
        logger.info(f"检索中: {query[:80]}...")
        # viewers 收敛成 list（Iterable 不可重复消费）
        viewers_list: list | None = list(viewers) if viewers is not None else None

        # 1. 查询改写（多查询）
        queries = self.query_rewriter.rewrite(query)

        # 确保原始查询始终参与检索（改写可能偏离原意）
        if query not in queries:
            queries.insert(0, query)

        # 2. 多查询向量检索（保留排名供 RRF 融合使用）
        vector_ranking: list[dict[str, Any]] = []
        seen_vec_ids = set()
        for q in queries:
            q_embedding = self.embedder.embed_query(q)
            results = self.vector_store.query(q_embedding, top_k=top_k, owners=viewers_list)
            for r in results:
                if r["id"] not in seen_vec_ids:
                    seen_vec_ids.add(r["id"])
                    r["match_type"] = "vector"
                    vector_ranking.append(r)
        # 多查询合并后按 distance 重排，作为向量路的最终排名
        vector_ranking.sort(key=lambda x: x.get("distance", 1.0))
        logger.debug(f"向量检索: {len(queries)} 条查询 → {len(vector_ranking)} 条结果")

        # 3. 混合检索融合
        if settings.enable_bm25_rrf and self.bm25_index is not None:
            # 3a. BM25 全文检索（基于词频的加权打分）
            bm25_ranking = self.bm25_index.search(query, top_k=top_k * 3, viewers=viewers_list)
            logger.debug(f"BM25 检索: {len(bm25_ranking)} 条结果")

            # 3b. RRF 融合（Reciprocal Rank Fusion）
            all_results = rrf_fuse(
                [vector_ranking, bm25_ranking],
                k=settings.rrf_k,
                weights=[settings.rrf_vector_weight, settings.rrf_bm25_weight],
            )
            logger.debug(f"RRF 融合后: {len(all_results)} 条唯一文档")
        else:
            # 降级：向量路 + 朴素关键词过滤 union，按 distance 排序
            all_results = list(vector_ranking)
            seen_ids = set(seen_vec_ids)
            keywords = self._extract_keywords(query)
            if keywords:
                kw_results = self.vector_store.keyword_search(
                    keywords, top_k=top_k, owners=viewers_list
                )
                before = len(all_results)
                for r in kw_results:
                    if r["id"] not in seen_ids:
                        seen_ids.add(r["id"])
                        all_results.append(r)
                logger.debug(f"关键词检索: 关键词={keywords}，新增 {len(all_results) - before} 条")
            all_results.sort(key=lambda x: x.get("distance", 1.0))

        # 5. 文本级去重（overlap 可能导致不同 chunk 内容高度重叠）
        all_results = self._deduplicate(all_results)

        # 6. 重排序（距离阈值过滤）
        before_rerank = len(all_results)
        all_results = self.reranker.rerank(query, all_results)
        logger.debug(f"重排序过滤: {before_rerank} → {len(all_results)} 条")

        # 7. 截取 top_k
        all_results = all_results[:top_k]

        # 8. 上下文窗口扩展
        if settings.context_window_size > 0:
            all_results = self._expand_context(all_results, viewers=viewers_list)

        return all_results

    @staticmethod
    def _extract_keywords(query: str) -> list[str]:
        """从用户查询中提取关键短语，用于关键词精确匹配。

        不依赖分词库，使用规则提取法规文本中的典型模式：
        - "第X条/章/款/项" 等法条编号
        - 引号内的精确术语
        - 连续中文名词短语（4字以上）
        """
        keywords = []

        # 法条编号：第X条、第X章 等
        law_refs = re.findall(r"第[零一二三四五六七八九十百千\d]+[条章款项节]", query)
        keywords.extend(law_refs)

        # 引号内的精确术语
        quoted = re.findall(r"[\"\"\"\'\'](.*?)[\"\"\"\'\']", query)
        keywords.extend(q for q in quoted if len(q) >= 2)

        # 高频法规术语直接匹配（从 settings.domain_terms 读取，支持 .env 覆盖）
        for term in settings.domain_terms:
            if term in query:
                keywords.append(term)

        # 去重
        return list(dict.fromkeys(keywords))

    def _deduplicate(self, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """基于文本内容去重，消除 overlap 和多查询导致的高度重叠结果。"""
        if len(results) <= 1:
            return results

        threshold = settings.text_overlap_threshold
        deduplicated: list[dict[str, Any]] = []
        seen_texts: list[str] = []

        for item in results:
            text = item.get("text", "")
            is_dup = any(self._text_overlap_ratio(text, seen) > threshold for seen in seen_texts)
            if not is_dup:
                deduplicated.append(item)
                seen_texts.append(text)

        logger.debug(f"文本去重: {len(results)} → {len(deduplicated)} 条（阈值={threshold}）")
        return deduplicated

    @staticmethod
    def _text_overlap_ratio(a: str, b: str) -> float:
        """计算两段文本的重叠率，兼容中文（无空格分词）。

        策略：使用字符级 n-gram (bigram) 而非空格分词，
        因为中文文本没有空格分隔，split() 会把整段作为一个词。
        """
        if not a or not b:
            return 0.0
        shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
        if shorter in longer:
            return 1.0

        # 字符级 bigram 集合——对中英文都有效
        def bigrams(text: str) -> set:
            return {text[i : i + 2] for i in range(len(text) - 1)} if len(text) >= 2 else {text}

        set_a = bigrams(a)
        set_b = bigrams(b)
        if not set_a or not set_b:
            return 0.0
        intersection = set_a & set_b
        return len(intersection) / min(len(set_a), len(set_b))

    def _expand_context(
        self,
        results: list[dict[str, Any]],
        viewers: list | None = None,
    ) -> list[dict[str, Any]]:
        """
        上下文窗口扩展：根据 chunk_index 拉取相邻 chunk，
        将前后 N 个 chunk 的文本拼接到当前 chunk，增强上下文完整性。

        Step 025a：相邻 chunk 也按 ``viewers`` 过滤（避免越权拼出他人内容）。
        """
        window = settings.context_window_size
        if window <= 0:
            return results

        expanded = []
        for item in results:
            meta = item.get("metadata", {})
            source_name = meta.get("source_name", "")
            chunk_index = meta.get("chunk_index", -1)

            if chunk_index < 0 or not source_name:
                expanded.append(item)
                continue

            # 查询同一文档中相邻的 chunk
            neighbor_texts = self.vector_store.get_neighbor_chunks(
                source_name=source_name,
                chunk_index=chunk_index,
                window=window,
                owners=viewers,
            )

            if neighbor_texts:
                item = dict(item)  # 不改原对象
                # 保留原始命中文本，供引用摘要使用（不被相邻chunk污染）
                item["original_text"] = item["text"]
                item["text"] = "\n".join(neighbor_texts)
                item["metadata"] = dict(meta, context_expanded=True)

            expanded.append(item)

        return expanded
