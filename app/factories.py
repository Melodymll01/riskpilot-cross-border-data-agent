"""App 层工厂函数：从 ``Settings`` 装配每个 Port 的具体实现。

每个 ``build_*`` 都返回满足对应 ``domain.ports`` 协议的对象。AppContainer
默认调用这些工厂；测试可以绕过工厂直接注入 fake。

设计：
- 工厂只做"装配 + 配置传参"，不做业务逻辑校验
- 不在工厂内捕获异常；启动期失败应当传播以便尽早暴露配置错误
- LLM/检索类适配器内部已经懒构造底层 client，工厂调用安全（无真实网络）
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain.ports import (
    AuditLogPort,
    AuthPort,
    ChatPort,
    ConsolidationStatePort,
    DocumentLoaderPort,
    EmbedPort,
    EvidencePort,
    FactStorePort,
    KbDocumentRepoPort,
    MemoryJobSchedulerPort,
    MemoryPort,
    ProfileStorePort,
    ResearchPort,
    RetrievePort,
    RiskProfilePort,
    SummaryStorePort,
    TaskRepoPort,
    UserRepoPort,
    WebSearchPort,
)
from infra.audit import SqliteAuditLogRepo
from infra.auth import AnonymousProvider, AuthService, GitHubOAuthProvider, JwtIssuer
from infra.chat import OpenAIChatAdapter
from infra.evidence import MockEvidenceClient
from infra.kb import ChromaKbRepo, UnifiedLoaderAdapter
from infra.memory import (
    ChromaFactStore,
    ConsolidationWorker,
    TaskBackedMemory,
    ThreadPoolMemoryScheduler,
)
from infra.research import AgenticResearchAdapter
from infra.risk_profile import StubRiskProfileService
from infra.search import EmbedderAdapter, HybridRetrieverAdapter
from infra.storage import (
    SqliteConsolidationStateStore,
    SqliteProfileStore,
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


def build_audit_log(
    settings: Settings, *, pool: SqliteConnectionPool | None = None
) -> AuditLogPort:
    """构造 ``AuditLogPort`` 实现：默认 ``SqliteAuditLogRepo`` 复用同一连接池。"""
    return SqliteAuditLogRepo(pool or build_sqlite_pool(settings))


def build_embedder(_settings: Settings) -> EmbedPort:
    return EmbedderAdapter()


def build_chat(_settings: Settings) -> ChatPort:
    return OpenAIChatAdapter()


def build_retriever(_settings: Settings) -> RetrievePort:
    return HybridRetrieverAdapter()


def build_web_search(_settings: Settings) -> WebSearchPort:
    return DuckDuckGoAdapter()


def build_evidence(_settings: Settings) -> EvidencePort:
    return MockEvidenceClient()


def build_risk_profile(_settings: Settings) -> RiskProfilePort:
    """默认返回占位实现：evidence-state 模型部署后在此切到 HTTP client。"""
    return StubRiskProfileService(mode="raise")


def build_memory(
    settings: Settings,
    *,
    task_repo: TaskRepoPort,
    chat: ChatPort | None = None,
    fact_store: FactStorePort | None = None,
    embedder: EmbedPort | None = None,
    profile_store: ProfileStorePort | None = None,
    state_store: ConsolidationStatePort | None = None,
) -> MemoryPort | None:
    """构造 ``MemoryPort``（S-030a L1 + S-030b L2 + S-030c L4 + S-030d L3/遗忘）；禁用时返回 None。

    None 是合法状态：表示"记忆关闭"，装配器据此退回无状态旧行为。
    L2 摘要依赖 summary_store + chat；L4 语义召回依赖 fact_store + embedder；
    L3 画像依赖 profile_store；主动遗忘需 state_store 清固化水位。
    任一缺失则该层静默退化。容器一般传入共享的 fact_store/embedder/state_store，
    以与固化 worker 复用同一 Chroma collection / 连接池。
    """
    if not settings.memory_enabled:
        return None
    summary_store = build_summary_store(settings, task_repo=task_repo)
    summary_chat: ChatPort | None = None
    if settings.memory_summary_enabled:
        summary_chat = chat if chat is not None else build_chat(settings)
    if settings.memory_consolidation_enabled:
        if fact_store is None:
            fact_store = build_fact_store(settings)
        if embedder is None:
            embedder = build_embedder(settings)
        if state_store is None:
            state_store = build_consolidation_state_store(settings, task_repo=task_repo)
    if profile_store is None:
        profile_store = build_profile_store(settings, task_repo=task_repo)
    return TaskBackedMemory(
        task_repo,
        summary_store=summary_store,
        chat=summary_chat,
        fact_store=fact_store,
        embedder=embedder,
        profile_store=profile_store,
        state_store=state_store,
        l1_ttl_days=settings.memory_l1_ttl_days,
        l2_ttl_days=settings.memory_l2_ttl_days,
        l4_ttl_days=settings.memory_l4_ttl_days,
        summary_threshold=settings.memory_summary_threshold,
    )


def build_profile_store(
    settings: Settings, *, task_repo: TaskRepoPort
) -> ProfileStorePort | None:
    """构造 L3 画像存储；禁用记忆 / 画像时返回 None。复用 task 连接池。"""
    if not (settings.memory_enabled and settings.memory_profile_enabled):
        return None
    if isinstance(task_repo, SqliteTaskRepo):
        return SqliteProfileStore(task_repo._pool)  # noqa: SLF001 — 同包复用连接池
    return SqliteProfileStore(build_sqlite_pool(settings))


def build_summary_store(
    settings: Settings, *, task_repo: TaskRepoPort
) -> SummaryStorePort | None:
    """构造 L2 摘要存储；禁用摘要时返回 None。

    复用 ``SqliteTaskRepo`` 的连接池，与 task 同库同事务边界。
    """
    if not (settings.memory_enabled and settings.memory_summary_enabled):
        return None
    if isinstance(task_repo, SqliteTaskRepo):
        return SqliteSummaryStore(task_repo._pool)  # noqa: SLF001 — 同包复用连接池
    return SqliteSummaryStore(build_sqlite_pool(settings))


def build_memory_scheduler(
    settings: Settings,
    *,
    memory: MemoryPort | None,
    consolidation_worker: ConsolidationWorker | None = None,
) -> MemoryJobSchedulerPort | None:
    """构造记忆后台调度器；记忆禁用 / （摘要与固化均禁用）时返回 None。"""
    if memory is None:
        return None
    if not settings.memory_summary_enabled and consolidation_worker is None:
        return None
    return ThreadPoolMemoryScheduler(
        memory,
        summary_threshold=settings.memory_summary_threshold,
        consolidation_worker=consolidation_worker,
    )


def build_fact_store(settings: Settings) -> FactStorePort | None:
    """构造 L4 语义事实存储（独立 Chroma collection）；记忆/固化禁用时返回 None。"""
    if not (settings.memory_enabled and settings.memory_consolidation_enabled):
        return None
    return ChromaFactStore()


def build_consolidation_state_store(
    settings: Settings, *, task_repo: TaskRepoPort
) -> ConsolidationStatePort | None:
    """构造 L4 固化进度水位存储；记忆/固化禁用时返回 None。复用 task 连接池。"""
    if not (settings.memory_enabled and settings.memory_consolidation_enabled):
        return None
    if isinstance(task_repo, SqliteTaskRepo):
        return SqliteConsolidationStateStore(task_repo._pool)  # noqa: SLF001 — 同包复用连接池
    return SqliteConsolidationStateStore(build_sqlite_pool(settings))


def build_consolidation_worker(
    settings: Settings,
    *,
    task_repo: TaskRepoPort,
    fact_store: FactStorePort | None = None,
    embedder: EmbedPort | None = None,
    chat: ChatPort | None = None,
    state_store: ConsolidationStatePort | None = None,
) -> ConsolidationWorker | None:
    """构造 L4 提取-验证-巩固 worker；记忆/固化禁用或依赖缺失时返回 None。

    依赖（fact_store/embedder/chat/state_store）未传入时按配置自建；
    容器一般传入与 ``build_memory`` 共享的实例，保证读写同一 collection。
    """
    if not (settings.memory_enabled and settings.memory_consolidation_enabled):
        return None
    fact_store = fact_store if fact_store is not None else build_fact_store(settings)
    state_store = (
        state_store
        if state_store is not None
        else build_consolidation_state_store(settings, task_repo=task_repo)
    )
    if fact_store is None or state_store is None:
        return None
    embedder = embedder if embedder is not None else build_embedder(settings)
    chat = chat if chat is not None else build_chat(settings)
    return ConsolidationWorker(
        task_repo=task_repo,
        fact_store=fact_store,
        embedder=embedder,
        chat=chat,
        state_store=state_store,
        min_backlog=settings.memory_consolidation_min_backlog,
        salience_threshold=settings.memory_fact_salience_threshold,
        dedup_threshold=settings.memory_fact_dedup_threshold,
        conflict_threshold=settings.memory_fact_conflict_threshold,
        fact_cap_per_owner=settings.memory_fact_cap_per_owner,
        decay_lambda=settings.memory_decay_lambda,
    )


def build_research(_settings: Settings) -> ResearchPort:
    """构造 ``ResearchPort`` 实现：默认 ``AgenticResearchAdapter``（包 v1 引擎）。

    适配器内部懒加载 v1 ``AgenticRAGAgent``（含 CrossEncoder 等重型组件），
    故工厂调用安全（无真实网络 / 模型加载）。
    """
    return AgenticResearchAdapter()


def build_kb_repo(_settings: Settings) -> KbDocumentRepoPort:
    """构造 ``KbDocumentRepoPort`` 实现：当前默认 ``ChromaKbRepo``。

    chromadb ``PersistentClient`` 在底层按 ``persist_dir`` 缓存实例，因此即使
    检索侧 ``HybridRetrieverAdapter`` 也持有 ``VectorStore``，这里再 new 一个
    ``VectorStore`` 仍指向同一个 collection，写入对检索可见。
    """
    from retrieval.search.vector_store import VectorStore

    return ChromaKbRepo(vector_store=VectorStore())


def build_document_loader(_settings: Settings) -> DocumentLoaderPort:
    """构造 ``DocumentLoaderPort`` 实现：当前默认 ``UnifiedLoaderAdapter``。

    包 v1 ``ingestion.UnifiedLoader`` + v1 ``processing.metadata.build_chunks``，
    对外只暴露 ``list[KbChunk]`` 返回值。
    """
    return UnifiedLoaderAdapter()


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


__all__ = [
    "build_audit_log",
    "build_auth",
    "build_chat",
    "build_consolidation_state_store",
    "build_consolidation_worker",
    "build_document_loader",
    "build_embedder",
    "build_evidence",
    "build_fact_store",
    "build_kb_repo",
    "build_memory",
    "build_memory_scheduler",
    "build_profile_store",
    "build_research",
    "build_retriever",
    "build_risk_profile",
    "build_sqlite_pool",
    "build_summary_store",
    "build_task_repo",
    "build_user_repo",
    "build_web_search",
]
