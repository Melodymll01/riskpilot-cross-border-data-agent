"""知识服务层：封装知识库的完整能力，供 HTTP 路由和多 Agent 系统调用。

本模块是整个 RAG 系统的核心服务入口。它将知识入库、检索、问答三大能力
封装为纯 Python 接口，不依赖任何 Web 框架。

使用方式：
    1. HTTP 路由层调用（当前的 api/routes.py）
    2. 多 Agent 系统中的 Agent 直接 import 调用（毕设场景）
    3. 作为独立的知识检索微服务

核心设计原则：
    - 无 FastAPI 依赖，纯 Python 类
    - 所有方法都是同步的（调用方按需用 asyncio.to_thread 包装）
    - 组件通过构造函数注入，支持替换实现
"""

import logging
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

from config import settings
from retrieval.search.embedder import Embedder
from retrieval.search.vector_store import VectorStore
from retrieval.search.retriever import Retriever
from retrieval.generation.qa_chain import QAChain, QAResult
from retrieval.search.query_rewriter import QueryRewriter
from retrieval.agent.agentic_rag import AgenticRAGAgent, AgenticRAGResult
from typing import Generator

logger = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    """纯检索结果（不含 LLM 生成的回答），供 Agent 自行推理使用。"""
    chunks: List[Dict[str, Any]]
    query_used: List[str]  # 实际使用的查询（含改写后的）


class KnowledgeService:
    """知识库检索 / 问答 / 深度研究服务层（仅读）。

    入库管理面已迁移至 ``app/use_cases/kb_management.py:KbManagementUseCase``
    （Step 016b/c 重构，走 ``container.kb_management`` 绑 ``KbDocumentRepoPort``
    + ``DocumentLoaderPort`` + ``EmbedPort``）；该类只保留检索 / 问答 /
    Agentic RAG 三个能力，给老 ``/api/retrieve`` ``/api/ask`` ``/api/research``
    入口与 benchmark 脚本使用。

        from service import KnowledgeService
        ks = KnowledgeService()

        # 法规检索 Agent — 只检索，不生成回答
        results = ks.retrieve("数据出境安全评估的触发条件", top_k=5)

        # 合规问答 Agent — 检索 + 生成回答
        answer = ks.ask("个人信息出境需要哪些手续？")
    """

    def __init__(
        self,
        embedder: Optional[Embedder] = None,
        vector_store: Optional[VectorStore] = None,
        retriever: Optional[Retriever] = None,
        qa_chain: Optional[QAChain] = None,
    ):
        """初始化知识服务，支持组件注入（不传则使用默认实例）。"""
        self.embedder = embedder or Embedder()
        self.vector_store = vector_store or VectorStore()

        query_rewriter = QueryRewriter()

        # 根据配置选择重排序器
        if settings.enable_reranker:
            try:
                from retrieval.search.reranker import CrossEncoderReranker
                reranker = CrossEncoderReranker(
                    model_name=settings.reranker_model,
                    device=settings.reranker_device,
                    score_threshold=settings.reranker_score_threshold,
                )
            except Exception as e:
                logger.warning(f"Cross-Encoder 加载失败，回退到距离阈值重排序: {e}")
                from retrieval.search.reranker import DistanceThresholdReranker
                reranker = DistanceThresholdReranker()
        else:
            from retrieval.search.reranker import DistanceThresholdReranker
            reranker = DistanceThresholdReranker()

        self.retriever = retriever or Retriever(
            embedder=self.embedder,
            vector_store=self.vector_store,
            query_rewriter=query_rewriter,
            reranker=reranker,
        )
        self.qa_chain = qa_chain or QAChain()

        # Agentic RAG Agent（深度研究模式）
        self.agentic_agent = AgenticRAGAgent(
            embedder=self.embedder,
            vector_store=self.vector_store,
            reranker=reranker,
        )

        logger.info("KnowledgeService 初始化完成（含 Agentic RAG Agent）")

    # ==================== 检索能力（核心：供 Agent 调用） ====================

    def retrieve(self, query: str, top_k: int = 5,
                 category: Optional[str] = None) -> RetrievalResult:
        """纯知识检索：只返回相关文档片段，不调用 LLM 生成回答。

        这是多 Agent 系统中最常用的接口。各 Agent 拿到检索结果后
        可以用自己的 prompt 和推理逻辑来处理。

        Args:
            query: 检索查询
            top_k: 返回结果数
            category: 可选的分类过滤（如 "法规" 只检索法规类文档）

        Returns:
            RetrievalResult 包含检索到的 chunks 和实际使用的查询
        """
        top_k = max(1, min(top_k, settings.max_top_k))

        # 获取改写后的查询（用于返回给调用方做参考）
        queries = self.retriever.query_rewriter.rewrite(query)
        if query not in queries:
            queries.insert(0, query)

        results = self.retriever.retrieve(query, top_k=top_k)

        # 按分类过滤（如果指定了 category）
        if category:
            results = [
                r for r in results
                if r.get("metadata", {}).get("category", "") == category
            ]

        return RetrievalResult(chunks=results, query_used=queries)

    # ==================== 问答能力 ====================

    def ask(self, question: str, top_k: int = 5,
            category: Optional[str] = None) -> QAResult:
        """检索 + LLM 生成回答（完整 RAG 流程）。

        Args:
            question: 用户问题
            top_k: 检索结果数
            category: 可选的分类过滤

        Returns:
            QAResult 包含回答、引用和置信度
        """
        retrieval = self.retrieve(question, top_k=top_k, category=category)
        return self.qa_chain.generate(question, retrieval.chunks)

    def ask_stream(self, question: str, top_k: int = 5,
                   category: Optional[str] = None) -> Generator:
        """流式问答：检索 + LLM 流式生成回答。

        Yields:
            str: 文本片段
            QAResult: 最后一个 yield，包含完整引用信息
        """
        retrieval = self.retrieve(question, top_k=top_k, category=category)
        yield from self.qa_chain.generate_stream(question, retrieval.chunks)

    # ==================== Agentic RAG 深度研究 ====================

    def research(
        self,
        query: str,
        mode: str = "report",
        top_k: int = 8,
        enable_web_search: bool = True,
    ) -> AgenticRAGResult:
        """Agentic RAG 深度研究：自我反思型检索 + 生成。

        与普通 ask() 的区别：
        - 多轮反思检索（最多 3 轮），自动评估质量并换词重搜
        - 质量不足时自动联网搜索补齐
        - 支持生成带引用的深度报告

        Args:
            query: 用户问题
            mode: "report" 深度报告 / "qa" 增强问答
            top_k: 每轮检索结果数
            enable_web_search: 是否允许联网搜索
        """
        return self.agentic_agent.research(
            query=query,
            mode=mode,
            top_k=top_k,
            enable_web_search=enable_web_search,
        )

    def research_stream(
        self,
        query: str,
        mode: str = "report",
        top_k: int = 8,
        enable_web_search: bool = True,
    ) -> Generator:
        """流式 Agentic RAG 深度研究。

        Yields:
            dict 事件对象（step / token / result）
        """
        yield from self.agentic_agent.research_stream(
            query=query,
            mode=mode,
            top_k=top_k,
            enable_web_search=enable_web_search,
        )
