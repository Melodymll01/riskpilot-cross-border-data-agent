"""retrieval 包：检索、生成、Agent 三大子模块。

子包结构：
    retrieval.search      — 向量检索管线（Embedder, VectorStore, Retriever, Reranker, QueryRewriter）
    retrieval.generation   — LLM 生成（ChatClient, QAChain, ReportGenerator）
    retrieval.agent        — Agentic RAG（AgenticRAGAgent, QuestionClassifier, QueryTransformer, EvidenceChecker, WebSearcher）
"""

# search
from retrieval.search.embedder import Embedder
from retrieval.search.vector_store import VectorStore
from retrieval.search.retriever import Retriever
from retrieval.search.reranker import BaseReranker, PassthroughReranker
from retrieval.search.query_rewriter import QueryRewriter

# generation
from retrieval.generation.qa_chain import QAChain
from retrieval.generation.chat_client import ChatClient

# agent
from retrieval.agent.agentic_rag import AgenticRAGAgent

__all__ = [
    "Embedder", "VectorStore", "Retriever",
    "BaseReranker", "PassthroughReranker", "QueryRewriter",
    "QAChain", "ChatClient", "AgenticRAGAgent",
]
