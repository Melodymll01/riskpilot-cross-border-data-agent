"""Construct the active SQLite, retrieval, model and identity adapters."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from langchain_core.language_models.chat_models import BaseChatModel

from domain.ports import (
    AuditLogPort,
    AuthPort,
    ChatPort,
    ClaimSupportVerifierPort,
    ConversationRunRepoPort,
    DocumentLoaderPort,
    DocumentParserPort,
    DocumentRepoPort,
    EmbedPort,
    EvidenceChunkerPort,
    EvidenceIndexPort,
    EvidenceQAGeneratorPort,
    FeedbackRepoPort,
    KbDocumentRepoPort,
    MemoryJobSchedulerPort,
    MemoryPort,
    MetricsPort,
    ObjectStorePort,
    ReadinessPort,
    RetrievePort,
    SummaryStorePort,
    TaskRepoPort,
    TracePort,
    UserRepoPort,
    WebSearchPort,
)
from infra.audit import SqliteAuditLogRepo
from infra.auth import AnonymousProvider, AuthService, GitHubOAuthProvider, JwtIssuer
from infra.chat import LangChainChatAdapter, build_chat_model
from infra.checkpoints import SqliteConversationCheckpointer
from infra.document_processing import RiskPilotDocumentParser
from infra.evidence import PageEvidenceChunker, SqliteEvidenceIndex
from infra.health import ApplicationReadiness
from infra.kb import ChromaKbRepo, UnifiedLoaderAdapter
from infra.memory import TaskBackedMemory, ThreadPoolMemoryScheduler
from infra.object_store import LocalObjectStore
from infra.observability import (
    CompositeTraceAdapter,
    LangSmithTraceAdapter,
    NoopMetricsAdapter,
    NoopTraceAdapter,
    OpenTelemetryTraceAdapter,
    PrometheusMetricsAdapter,
)
from infra.qa import StructuredClaimSupportVerifier, StructuredEvidenceQAGenerator
from infra.search import EmbedderAdapter, HybridRetrieverAdapter
from infra.storage import (
    SqliteConversationRunRepo,
    SqliteDocumentRepo,
    SqliteFeedbackRepo,
    SqliteSummaryStore,
    SqliteTaskRepo,
    SqliteUserRepo,
)
from infra.storage._db import SqliteConnectionPool
from infra.web import DuckDuckGoAdapter

if TYPE_CHECKING:
    from config import Settings


def build_sqlite_pool(settings: Settings) -> SqliteConnectionPool:
    """单例 SQLite 连接池：所有 repo 共享。"""
    return SqliteConnectionPool(settings.sqlite_db_path)


def build_user_repo(
    settings: Settings, *, pool: SqliteConnectionPool | None = None
) -> UserRepoPort:
    return SqliteUserRepo(pool or build_sqlite_pool(settings))


def build_task_repo(
    settings: Settings, *, pool: SqliteConnectionPool | None = None
) -> TaskRepoPort:
    return SqliteTaskRepo(pool or build_sqlite_pool(settings))


def build_conversation_run_repo(
    settings: Settings,
    *,
    pool: SqliteConnectionPool | None = None,
) -> ConversationRunRepoPort:
    return SqliteConversationRunRepo(pool or build_sqlite_pool(settings))


def build_conversation_checkpointer(settings: Settings) -> SqliteConversationCheckpointer:
    return SqliteConversationCheckpointer(settings.langgraph_checkpoint_db_path)


def build_document_parser(_settings: Settings) -> DocumentParserPort:
    import time

    return RiskPilotDocumentParser(clock=time.time)


def build_evidence_chunker(settings: Settings) -> EvidenceChunkerPort:
    import time

    return PageEvidenceChunker(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        clock=time.time,
    )


def build_audit_log(
    settings: Settings, *, pool: SqliteConnectionPool | None = None
) -> AuditLogPort:
    """构造 ``AuditLogPort`` 实现：默认 ``SqliteAuditLogRepo`` 复用同一连接池。"""
    return SqliteAuditLogRepo(pool or build_sqlite_pool(settings))


def build_feedback_repo(
    settings: Settings, *, pool: SqliteConnectionPool | None = None
) -> FeedbackRepoPort:
    """构造 ``FeedbackRepoPort`` 实现：复用同一 SQLite 连接池。"""
    return SqliteFeedbackRepo(pool or build_sqlite_pool(settings))


def build_embedder(settings: Settings) -> EmbedPort:
    from retrieval.search.embedder import Embedder

    return EmbedderAdapter(Embedder(config=settings))


def build_chat(settings: Settings) -> ChatPort:
    qwen_json_mode = (
        settings.llm_provider == "api"
        and settings.effective_chat_model.lower().startswith(("qwen3", "qwq"))
    )
    return LangChainChatAdapter(
        build_chat_model(settings),
        json_extra_body={"enable_thinking": False} if qwen_json_mode else None,
    )


def build_agent_model(settings: Settings) -> BaseChatModel:
    """Agent 与 ChatPort 共用相同的模型路由与配置。"""
    return build_chat_model(settings)


def build_evidence_qa_generator(
    _settings: Settings,
    *,
    chat: ChatPort,
) -> EvidenceQAGeneratorPort:
    return StructuredEvidenceQAGenerator(chat)


def build_claim_support_verifier(
    _settings: Settings,
    *,
    chat: ChatPort,
) -> ClaimSupportVerifierPort:
    return StructuredClaimSupportVerifier(chat)


def build_retriever(settings: Settings) -> RetrievePort:
    return HybridRetrieverAdapter(model_settings=settings)


def build_web_search(_settings: Settings) -> WebSearchPort:
    return DuckDuckGoAdapter()


def build_trace(settings: Settings) -> TracePort:
    global_switches = {
        "LANGSMITH_TRACING": os.getenv("LANGSMITH_TRACING", ""),
        "LANGCHAIN_TRACING_V2": os.getenv("LANGCHAIN_TRACING_V2", ""),
    }
    enabled_switches = [
        name
        for name, value in global_switches.items()
        if value.strip().lower() in {"1", "true", "yes", "on"}
    ]
    if enabled_switches:
        raise ValueError(
            "禁止使用 LangSmith SDK 全局追踪开关 "
            f"{', '.join(enabled_switches)}；请改用 RISK_PILOT_LANGSMITH_ENABLED，"
            "确保 Trace 经过隐私 Adapter"
        )
    adapters: list[TracePort] = []
    if settings.otel_enabled:
        adapters.append(
            OpenTelemetryTraceAdapter(
                service_name=settings.otel_service_name,
                endpoint=settings.otel_exporter_otlp_endpoint,
                sampling_rate=settings.otel_sampling_rate,
                hash_salt=settings.observability_hash_salt,
            )
        )
    if settings.risk_pilot_langsmith_enabled:
        adapters.append(
            LangSmithTraceAdapter(
                api_key=settings.langsmith_api_key or "",
                endpoint=settings.langsmith_endpoint,
                project=settings.langsmith_project,
                sampling_rate=settings.langsmith_sampling_rate,
                hash_salt=settings.langsmith_hash_salt or "",
            )
        )
    if not adapters:
        return NoopTraceAdapter()
    if len(adapters) == 1:
        return adapters[0]
    return CompositeTraceAdapter(*adapters)


def build_metrics(settings: Settings) -> MetricsPort:
    if not settings.prometheus_enabled:
        return NoopMetricsAdapter()
    return PrometheusMetricsAdapter()


def build_summary_store(settings: Settings, *, task_repo: TaskRepoPort) -> SummaryStorePort | None:
    """构造 L2 摘要存储；禁用摘要时返回 None。

    复用 ``SqliteTaskRepo`` 的连接池，与 task 同库同事务边界。
    """
    if not (settings.memory_enabled and settings.memory_summary_enabled):
        return None
    if isinstance(task_repo, SqliteTaskRepo):
        return SqliteSummaryStore(task_repo._pool)  # noqa: SLF001 — 同包复用连接池
    return SqliteSummaryStore(build_sqlite_pool(settings))


def build_kb_repo(settings: Settings) -> KbDocumentRepoPort:
    """构造 ``KbDocumentRepoPort`` 实现：当前默认 ``ChromaKbRepo``。

    chromadb ``PersistentClient`` 在底层按 ``persist_dir`` 缓存实例，因此即使
    检索侧 ``HybridRetrieverAdapter`` 也持有 ``VectorStore``，这里再 new 一个
    ``VectorStore`` 仍指向同一个 collection，写入对检索可见。
    """
    from retrieval.search.vector_store import VectorStore

    return ChromaKbRepo(vector_store=VectorStore(config=settings))


def build_document_loader(settings: Settings) -> DocumentLoaderPort:
    """构造 ``DocumentLoaderPort`` 实现：当前默认 ``UnifiedLoaderAdapter``。

    包 v1 ``ingestion.UnifiedLoader`` + v1 ``processing.metadata.build_chunks``，
    对外只暴露 ``list[KbChunk]`` 返回值。
    """
    from processing.splitter import TextSplitter

    return UnifiedLoaderAdapter(splitter=TextSplitter(settings.chunk_size, settings.chunk_overlap))


def build_auth(settings: Settings, user_repo: UserRepoPort) -> AuthPort:
    """组合 JwtIssuer + GitHubOAuthProvider + AnonymousProvider 为 AuthService。"""
    jwt_issuer = JwtIssuer(
        secret=settings.jwt_secret,
        ttl_seconds=settings.jwt_ttl_seconds,
    )
    providers: dict = {
        "github": GitHubOAuthProvider(
            client_id=settings.github_client_id,
            client_secret=settings.github_client_secret,
            redirect_uri=settings.github_redirect_uri,
        ),
    }
    return AuthService(
        providers=providers,
        jwt_issuer=jwt_issuer,
        user_repo=user_repo,
        anonymous=AnonymousProvider(),
    )


def build_document_repo(
    settings: Settings, *, pool: SqliteConnectionPool | None = None
) -> DocumentRepoPort:
    return SqliteDocumentRepo(pool or build_sqlite_pool(settings))


def build_evidence_index(
    settings: Settings, *, pool: SqliteConnectionPool | None = None
) -> EvidenceIndexPort:
    return SqliteEvidenceIndex(
        pool or build_sqlite_pool(settings), embedding_identity=settings.embedding_identity
    )


def build_object_store(settings: Settings) -> ObjectStorePort:
    return LocalObjectStore(settings.object_store_dir)


def build_readiness(settings: Settings, *, database: SqliteConnectionPool | None) -> ReadinessPort:
    return ApplicationReadiness(database=database)


def build_memory(
    settings: Settings, *, task_repo: TaskRepoPort, chat: ChatPort
) -> MemoryPort | None:
    if not settings.memory_enabled:
        return None
    return TaskBackedMemory(
        task_repo,
        summary_store=build_summary_store(settings, task_repo=task_repo),
        chat=chat,
        l1_ttl_days=settings.memory_l1_ttl_days,
        l2_ttl_days=settings.memory_l2_ttl_days,
        summary_threshold=settings.memory_summary_threshold,
        summary_max_tokens=settings.memory_summary_max_tokens,
    )


def build_memory_scheduler(
    settings: Settings, *, memory: MemoryPort | None
) -> MemoryJobSchedulerPort | None:
    if memory is None or not settings.memory_summary_enabled:
        return None
    return ThreadPoolMemoryScheduler(memory, summary_threshold=settings.memory_summary_threshold)
