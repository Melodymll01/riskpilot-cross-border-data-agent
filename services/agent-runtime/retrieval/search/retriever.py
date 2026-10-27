"""检索模块：组合 QueryRewriter + Embedder + VectorStore + Reranker 完成端到端检索。

包含关键 RAG 优化：
1. 多查询检索（Multi-Query）：对改写后的多条查询分别检索，合并结果
2. 混合检索（Hybrid Search）：向量语义检索 + BM25 关键词检索，RRF 融合排名
3. 上下文窗口扩展（Context Window）：基于 chunk_index 拉取相邻 chunk 补充上下文
4. 结果去重：基于中文 bigram 的文本去重，消除 overlap 和多查询带来的重复
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from config import Settings, settings
from processing.metadata import PUBLIC_OWNER_MARKER
from retrieval.search.bm25_index import BM25Index
from retrieval.search.embedder import Embedder
from retrieval.search.fusion import rrf_fuse
from retrieval.search.query_rewriter import QueryRewriter
from retrieval.search.reranker import BaseReranker, DistanceThresholdReranker
from retrieval.search.vector_store import VectorStore

if TYPE_CHECKING:
    from domain.ports import RunControlPort

logger = logging.getLogger(__name__)

_TEMPLATE_QUERY_TERMS = (
    "模板",
    "申请表",
    "申报表",
    "表格",
    "表单",
    "填写",
    "材料",
    "清单",
    "报告",
    "附件",
    "格式",
    "字段",
)
_SUPPLEMENTAL_PDF_KINDS = frozenset({"template", "template_table", "low_information"})
_SECTION_QUERY_TERMS = (
    "流程",
    "步骤",
    "阶段",
    "办理顺序",
    "先后关系",
    "怎么申报",
    "如何申报",
)


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
        *,
        config: Settings | None = None,
    ):
        self._settings = config or settings
        self.embedder = embedder
        self.vector_store = vector_store
        self.reranker = reranker or DistanceThresholdReranker(self._settings.distance_threshold)
        self.query_rewriter = query_rewriter or QueryRewriter(config=self._settings)
        # BM25 索引：懒加载，向量库数据变动时自动失效重建
        self.bm25_index = BM25Index(vector_store) if self._settings.enable_bm25_rrf else None
        if self.bm25_index is not None:
            self.vector_store.register_change_listener(self.bm25_index.mark_dirty)

    def retrieve(
        self,
        query: str,
        top_k: int | None = None,
        viewers: Iterable[str | None] | None = None,
        runtime_control: RunControlPort | None = None,
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
        top_k = self._settings.top_k if top_k is None else top_k
        vector_candidate_k = min(
            top_k * self._settings.retrieval_vector_candidate_multiplier,
            self._settings.retrieval_candidate_max,
        )
        logger.info(f"检索中: {query[:80]}...")
        # viewers 收敛成 list（Iterable 不可重复消费）
        viewers_list: list | None = list(viewers) if viewers is not None else None

        # 1. 查询改写（多查询）
        queries = (
            self.query_rewriter.rewrite(query, runtime_control=runtime_control)
            if runtime_control is not None
            else self.query_rewriter.rewrite(query)
        )
        if runtime_control is not None:
            runtime_control.check(stage="tool")

        # 确保原始查询始终参与检索（改写可能偏离原意）
        if query not in queries:
            queries.insert(0, query)

        # 2. 多查询向量检索（保留排名供 RRF 融合使用）
        vector_ranking: list[dict[str, Any]] = []
        seen_vec_ids = set()
        for q in queries:
            if runtime_control is not None:
                runtime_control.check(stage="tool")
            q_embedding = (
                self.embedder.embed_query(q, runtime_control=runtime_control)
                if runtime_control is not None
                else self.embedder.embed_query(q)
            )
            results = self.vector_store.query(
                q_embedding,
                top_k=vector_candidate_k,
                owners=viewers_list,
            )
            if runtime_control is not None:
                runtime_control.check(stage="tool")
            for r in results:
                if r["id"] not in seen_vec_ids:
                    seen_vec_ids.add(r["id"])
                    r["match_type"] = "vector"
                    vector_ranking.append(r)
        # 多查询合并后按 distance 重排，作为向量路的最终排名
        vector_ranking.sort(key=lambda x: x.get("distance", 1.0))
        logger.debug(f"向量检索: {len(queries)} 条查询 → {len(vector_ranking)} 条结果")

        # 3. 混合检索融合
        if self._settings.enable_bm25_rrf and self.bm25_index is not None:
            # 3a. BM25 全文检索（基于词频的加权打分）
            bm25_ranking = self.bm25_index.search(
                query,
                top_k=min(
                    top_k * self._settings.retrieval_bm25_candidate_multiplier,
                    self._settings.retrieval_candidate_max,
                ),
                viewers=viewers_list,
            )
            logger.debug(f"BM25 检索: {len(bm25_ranking)} 条结果")

            # 3b. RRF 融合（Reciprocal Rank Fusion）
            all_results = rrf_fuse(
                [vector_ranking, bm25_ranking],
                k=self._settings.rrf_k,
                weights=[self._settings.rrf_vector_weight, self._settings.rrf_bm25_weight],
            )
            logger.debug(f"RRF 融合后: {len(all_results)} 条唯一文档")
        else:
            # 降级：向量路 + 朴素关键词过滤 union，按 distance 排序
            all_results = list(vector_ranking)
            seen_ids = set(seen_vec_ids)
            keywords = self._extract_keywords(query, domain_terms=self._settings.domain_terms)
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
        all_results = (
            self.reranker.rerank(
                query,
                all_results,
                runtime_control=runtime_control,
            )
            if runtime_control is not None
            else self.reranker.rerank(query, all_results)
        )
        if runtime_control is not None:
            runtime_control.check(stage="tool")
        logger.debug(f"重排序过滤: {before_rerank} → {len(all_results)} 条")

        # 普通法规问答优先正文；只有问题明确询问模板/表单/材料时才维持模板原排名。
        all_results = self._prefer_primary_pdf_content(query, all_results)

        # 7. 流程问题先按父章节去重并回填，再截取 top_k，避免多个 child 占满名额。
        all_results = self._select_top_results(query, all_results, top_k=top_k)

        # 8. 上下文窗口扩展
        if self._settings.context_window_size > 0:
            all_results = self._expand_context(
                all_results,
                query=query,
                viewers=viewers_list,
            )

        return all_results

    @staticmethod
    def _prefer_primary_pdf_content(
        query: str, results: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """稳定下沉 PDF 模板/低信息页，仍保留为正文不足时的候选。"""
        if any(term in query for term in _TEMPLATE_QUERY_TERMS):
            return results
        primary: list[dict[str, Any]] = []
        supplemental: list[dict[str, Any]] = []
        for item in results:
            content_kind = str((item.get("metadata") or {}).get("content_kind") or "")
            target = supplemental if content_kind in _SUPPLEMENTAL_PDF_KINDS else primary
            target.append(item)
        return [*primary, *supplemental]

    @classmethod
    def _select_top_results(
        cls,
        query: str,
        results: list[dict[str, Any]],
        *,
        top_k: int,
    ) -> list[dict[str, Any]]:
        if not cls._is_section_query(query):
            return results[:top_k]
        selected: list[dict[str, Any]] = []
        seen: set[tuple[str, ...]] = set()
        for item in results:
            meta = item.get("metadata") or {}
            parent_id = str(meta.get("parent_section_id") or "")
            key: tuple[str, ...]
            if parent_id:
                key = (
                    "parent",
                    str(meta.get("owner_id") or ""),
                    str(meta.get("source_name") or ""),
                    parent_id,
                )
            else:
                key = ("chunk", str(item.get("id") or ""))
            if key in seen:
                continue
            seen.add(key)
            selected.append(item)
            if len(selected) >= top_k:
                break
        return selected

    @staticmethod
    def _extract_keywords(query: str, *, domain_terms: list[str] | None = None) -> list[str]:
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

        # 高频法规术语直接匹配（从 self._settings.domain_terms 读取，支持 .env 覆盖）
        for term in settings.domain_terms if domain_terms is None else domain_terms:
            if term in query:
                keywords.append(term)

        # 去重
        return list(dict.fromkeys(keywords))

    def _deduplicate(self, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """基于文本内容去重，消除 overlap 和多查询导致的高度重叠结果。"""
        if len(results) <= 1:
            return results

        threshold = self._settings.text_overlap_threshold
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
        *,
        query: str,
        viewers: list | None = None,
    ) -> list[dict[str, Any]]:
        """
        上下文窗口扩展：根据 chunk_index 拉取相邻 chunk，
        将前后 N 个 chunk 的文本拼接到当前 chunk，增强上下文完整性。

        Step 025a：相邻 chunk 也按 ``viewers`` 过滤（避免越权拼出他人内容）。
        """
        window = self._settings.context_window_size
        if window <= 0:
            return results

        expanded = []
        expand_parent_section = self._is_section_query(query)
        expanded_parent_ids: set[tuple[str, str, str]] = set()
        remaining_chars = self._settings.retrieval_context_total_max_chars
        for item in results:
            if remaining_chars <= 0:
                break
            meta = item.get("metadata", {})
            source_name = meta.get("source_name", "")
            chunk_index = meta.get("chunk_index", -1)
            parent_section_id = str(meta.get("parent_section_id") or "")
            raw_owner = str(meta.get("owner_id") or PUBLIC_OWNER_MARKER)
            exact_owner = None if raw_owner == PUBLIC_OWNER_MARKER else raw_owner

            if chunk_index < 0 or not source_name:
                expanded.append(item)
                continue

            if expand_parent_section and parent_section_id:
                parent_key = (raw_owner, str(source_name), parent_section_id)
                if parent_key in expanded_parent_ids:
                    continue
                section_chunks = self.vector_store.get_section_chunks(
                    source_name=str(source_name),
                    parent_section_id=parent_section_id,
                    owners=[exact_owner],
                )
                section_text = self._pack_section_context(
                    section_chunks,
                    seed_chunk_index=int(chunk_index),
                    max_chars=min(
                        self._settings.section_expansion_max_chars,
                        remaining_chars,
                    ),
                )
                if section_text:
                    expanded_parent_ids.add(parent_key)
                    item = dict(item)
                    seed_chunk_id = str(item.get("id") or "")
                    item["id"] = parent_section_id
                    item["original_text"] = item["text"]
                    item["text"] = section_text
                    item["metadata"] = dict(
                        meta,
                        context_expanded=True,
                        context_expansion_mode="parent_section",
                        seed_chunk_id=seed_chunk_id,
                    )
                    expanded.append(item)
                    remaining_chars -= len(section_text)
                    continue

            # 查询同一文档中相邻的 chunk
            neighbor_texts = self.vector_store.get_neighbor_chunks(
                source_name=source_name,
                chunk_index=chunk_index,
                window=window,
                owners=viewers,
            )

            if neighbor_texts:
                neighbor_text = self._join_texts_with_budget(neighbor_texts, remaining_chars)
                if not neighbor_text:
                    break
                item = dict(item)  # 不改原对象
                # 保留原始命中文本，供引用摘要使用（不被相邻chunk污染）
                item["original_text"] = item["text"]
                item["text"] = neighbor_text
                item["metadata"] = dict(meta, context_expanded=True)
                remaining_chars -= len(neighbor_text)

            expanded.append(item)

        return expanded

    @staticmethod
    def _is_section_query(query: str) -> bool:
        return any(term in query for term in _SECTION_QUERY_TERMS)

    @staticmethod
    def _pack_section_context(
        chunks: list[dict[str, Any]],
        *,
        seed_chunk_index: int,
        max_chars: int,
    ) -> str:
        if not chunks or max_chars <= 0:
            return ""
        ordered = sorted(
            chunks,
            key=lambda item: int((item.get("metadata") or {}).get("chunk_index", 0)),
        )
        if sum(len(str(item.get("text") or "")) + 2 for item in ordered) <= max_chars:
            return "\n\n".join(str(item.get("text") or "") for item in ordered if item.get("text"))

        ranked = sorted(
            enumerate(ordered),
            key=lambda pair: abs(
                int((pair[1].get("metadata") or {}).get("chunk_index", 0)) - seed_chunk_index
            ),
        )
        selected_indexes: list[int] = []
        used_chars = 0
        for ordered_index, item in ranked:
            text = str(item.get("text") or "")
            if not text:
                continue
            added = len(text) + (2 if selected_indexes else 0)
            if selected_indexes and used_chars + added > max_chars:
                continue
            selected_indexes.append(ordered_index)
            used_chars += added
            if used_chars >= max_chars:
                break
        return "\n\n".join(
            str(ordered[index].get("text") or "") for index in sorted(selected_indexes)
        )

    @staticmethod
    def _join_texts_with_budget(texts: list[str], max_chars: int) -> str:
        selected: list[str] = []
        used = 0
        for text in texts:
            added = len(text) + (1 if selected else 0)
            if selected and used + added > max_chars:
                break
            if not selected and len(text) > max_chars:
                return text[:max_chars]
            selected.append(text)
            used += added
        return "\n".join(selected)
