"""App 层工厂函数：从 ``Settings`` 装配每个 Port 的具体实现。

每个 ``build_*`` 都返回满足对应 ``domain.ports`` 协议的对象。AppContainer
默认调用这些工厂；测试可以绕过工厂直接注入 fake。

设计：
- 工厂只做"装配 + 配置传参"，不做业务逻辑校验
- 不在工厂内捕获异常；启动期失败应当传播以便尽早暴露配置错误
- LLM/检索类适配器内部已经懒构造底层 client，工厂调用安全（无真实网络）
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from langchain_core.language_models.chat_models import BaseChatModel

from domain.agent_workflow import AgentBudget
from domain.memory import MemoryRecallPolicy
from domain.ports import (
    AgentRunRepoPort,
    AssessmentRepoPort,
    AuditLogPort,
    AuthPort,
    BackgroundJobDispatcherPort,
    CaseFactRepoPort,
    CaseRepoPort,
    ChatPort,
    ClaimSupportVerifierPort,
    ConsolidationStatePort,
    DocumentLoaderPort,
    DocumentParserPort,
    DocumentRepoPort,
    EmbedPort,
    EvidenceChunkerPort,
    EvidenceIndexPort,
    EvidencePlannerPort,
    EvidenceQAGeneratorPort,
    FactProposalGeneratorPort,
    FactStorePort,
    FeedbackRepoPort,
    KbDocumentRepoPort,
    MemoryJobSchedulerPort,
    MemoryPort,
    MemorySettingsStorePort,
    MetricsPort,
    ObjectStorePort,
    PolicyRuleRepoPort,
    ProfileStorePort,
    ReadinessPort,
    ResearchPort,
    RetrievePort,
    RiskProfilePort,
    SummaryStorePort,
    TaskRepoPort,
    TracePort,
    UserRepoPort,
    VisualEmbedPort,
    VisualIndexPort,
    WebSearchPort,
    WorkflowRuntimePort,
    WorkspaceRepoPort,
)
from infra.agents import DeterministicEvidencePlanner, LangChainEvidencePlanner
from infra.agents.model import build_langchain_chat_model
from infra.audit import SqliteAuditLogRepo
from infra.auth import AnonymousProvider, AuthService, GitHubOAuthProvider, JwtIssuer
from infra.chat import OpenAIChatAdapter
from infra.document_processing import RiskPilotDocumentParser
from infra.evidence import PageEvidenceChunker, SqliteEvidenceIndex
from infra.health import ApplicationReadiness
from infra.kb import ChromaKbRepo, UnifiedLoaderAdapter
from infra.memory import (
    ChromaFactStore,
    ConsolidationWorker,
    TaskBackedMemory,
    ThreadPoolMemoryScheduler,
)
from infra.object_store import LocalObjectStore, S3ObjectStore
from infra.observability import (
    CompositeTraceAdapter,
    LangSmithTraceAdapter,
    NoopMetricsAdapter,
    NoopTraceAdapter,
    OpenTelemetryTraceAdapter,
    PrometheusMetricsAdapter,
)
from infra.qa import (
    StructuredClaimSupportVerifier,
    StructuredEvidenceQAGenerator,
    StructuredFactProposalGenerator,
)
from infra.research import LangGraphResearchAdapter
from infra.risk_profile import HttpRiskProfileClient
from infra.search import DeterministicEmbedder, EmbedderAdapter, HybridRetrieverAdapter
from infra.storage import (
    SqliteAgentRunRepo,
    SqliteAssessmentRepo,
    SqliteCaseFactRepo,
    SqliteCaseRepo,
    SqliteConsolidationStateStore,
    SqliteDocumentRepo,
    SqliteFeedbackRepo,
    SqliteMemorySettingsStore,
    SqlitePolicyRuleRepo,
    SqliteProfileStore,
    SqliteSummaryStore,
    SqliteTaskRepo,
    SqliteUserRepo,
    SqliteVisualIndex,
    SqliteWorkspaceRepo,
)
from infra.storage._db import SqliteConnectionPool
from infra.storage.sqlalchemy import (
    SqlAlchemyAgentRunRepo,
    SqlAlchemyAssessmentRepo,
    SqlAlchemyCaseFactRepo,
    SqlAlchemyCaseRepo,
    SqlAlchemyDatabase,
    SqlAlchemyDocumentRepo,
    SqlAlchemyEvidenceIndex,
    SqlAlchemyPolicyRuleRepo,
    SqlAlchemyWorkspaceRepo,
)
from infra.tasks import CeleryJobDispatcher, ManualJobDispatcher, build_celery_app
from infra.visual import ChineseCLIPEmbedder
from infra.web import DuckDuckGoAdapter
from infra.workflows import LangGraphWorkflowRuntime

if TYPE_CHECKING:
    from config import Settings
    from domain.ports import CaseAssessmentToolPort


def build_sqlite_pool(settings: Settings) -> SqliteConnectionPool:
    """单例 SQLite 连接池：所有 repo 共享。"""
    return SqliteConnectionPool(settings.sqlite_db_path)


def build_sqlalchemy_database(settings: Settings) -> SqlAlchemyDatabase:
    return SqlAlchemyDatabase(settings.database_url)


def build_readiness(
    settings: Settings,
    *,
    database: SqliteConnectionPool | SqlAlchemyDatabase | None,
) -> ReadinessPort:
    return ApplicationReadiness(
        database=database,
        redis_url=settings.redis_url or settings.celery_broker_url,
    )


def build_assessment_repo(
    settings: Settings,
    *,
    pool: SqliteConnectionPool | None = None,
    database: SqlAlchemyDatabase | None = None,
) -> AssessmentRepoPort:
    if settings.storage_backend == "postgres":
        return SqlAlchemyAssessmentRepo(database or build_sqlalchemy_database(settings))
    return SqliteAssessmentRepo(pool or build_sqlite_pool(settings))


def build_agent_run_repo(
    settings: Settings,
    *,
    pool: SqliteConnectionPool | None = None,
    database: SqlAlchemyDatabase | None = None,
) -> AgentRunRepoPort:
    if settings.storage_backend == "postgres":
        return SqlAlchemyAgentRunRepo(database or build_sqlalchemy_database(settings))
    return SqliteAgentRunRepo(pool or build_sqlite_pool(settings))


def build_user_repo(
    settings: Settings, *, pool: SqliteConnectionPool | None = None
) -> UserRepoPort:
    return SqliteUserRepo(pool or build_sqlite_pool(settings))


def build_task_repo(
    settings: Settings, *, pool: SqliteConnectionPool | None = None
) -> TaskRepoPort:
    return SqliteTaskRepo(pool or build_sqlite_pool(settings))


def build_workspace_repo(
    settings: Settings,
    *,
    pool: SqliteConnectionPool | None = None,
    database: SqlAlchemyDatabase | None = None,
) -> WorkspaceRepoPort:
    if settings.storage_backend == "postgres":
        return SqlAlchemyWorkspaceRepo(database or build_sqlalchemy_database(settings))
    return SqliteWorkspaceRepo(pool or build_sqlite_pool(settings))


def build_case_repo(
    settings: Settings,
    *,
    pool: SqliteConnectionPool | None = None,
    database: SqlAlchemyDatabase | None = None,
) -> CaseRepoPort:
    if settings.storage_backend == "postgres":
        return SqlAlchemyCaseRepo(database or build_sqlalchemy_database(settings))
    return SqliteCaseRepo(pool or build_sqlite_pool(settings))


def build_case_fact_repo(
    settings: Settings,
    *,
    pool: SqliteConnectionPool | None = None,
    database: SqlAlchemyDatabase | None = None,
) -> CaseFactRepoPort:
    if settings.storage_backend == "postgres":
        return SqlAlchemyCaseFactRepo(database or build_sqlalchemy_database(settings))
    return SqliteCaseFactRepo(pool or build_sqlite_pool(settings))


def build_policy_rule_repo(
    settings: Settings,
    *,
    pool: SqliteConnectionPool | None = None,
    database: SqlAlchemyDatabase | None = None,
) -> PolicyRuleRepoPort:
    if settings.storage_backend == "postgres":
        return SqlAlchemyPolicyRuleRepo(database or build_sqlalchemy_database(settings))
    return SqlitePolicyRuleRepo(pool or build_sqlite_pool(settings))


def build_document_repo(
    settings: Settings,
    *,
    pool: SqliteConnectionPool | None = None,
    database: SqlAlchemyDatabase | None = None,
) -> DocumentRepoPort:
    if settings.storage_backend == "postgres":
        return SqlAlchemyDocumentRepo(database or build_sqlalchemy_database(settings))
    return SqliteDocumentRepo(pool or build_sqlite_pool(settings))


def build_object_store(settings: Settings) -> ObjectStorePort:
    if settings.object_store_backend == "s3":
        return S3ObjectStore(
            bucket=settings.s3_bucket,
            endpoint_url=settings.s3_endpoint_url,
            access_key_id=settings.s3_access_key_id,
            secret_access_key=settings.s3_secret_access_key,
            region=settings.s3_region,
        )
    return LocalObjectStore(settings.object_store_dir)


def build_job_dispatcher(settings: Settings) -> BackgroundJobDispatcherPort:
    if settings.task_backend == "celery":
        return CeleryJobDispatcher(build_celery_app(settings))
    return ManualJobDispatcher()


def build_visual_index(
    settings: Settings, *, pool: SqliteConnectionPool | None = None
) -> VisualIndexPort:
    return SqliteVisualIndex(pool or build_sqlite_pool(settings))


def build_visual_embedder(settings: Settings) -> VisualEmbedPort:
    return ChineseCLIPEmbedder(settings.visual_model_name)


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


def build_evidence_index(
    settings: Settings,
    *,
    pool: SqliteConnectionPool | None = None,
    database: SqlAlchemyDatabase | None = None,
) -> EvidenceIndexPort:
    if settings.vector_backend == "pgvector":
        return SqlAlchemyEvidenceIndex(
            database or build_sqlalchemy_database(settings),
            embedding_dimensions=settings.embedding_dimensions,
        )
    return SqliteEvidenceIndex(pool or build_sqlite_pool(settings))


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
    if settings.embed_provider == "deterministic":
        if settings.embedding_dimensions is None:
            raise ValueError("deterministic embedding 必须配置 EMBEDDING_DIMENSIONS")
        return DeterministicEmbedder(settings.embedding_dimensions)
    return EmbedderAdapter()


def build_chat(_settings: Settings) -> ChatPort:
    return OpenAIChatAdapter(build_agent_model(_settings))


def build_agent_model(settings: Settings) -> BaseChatModel:
    """构造 LangChain 标准 ChatModel；只供 tool-calling Agent 使用。"""
    return build_langchain_chat_model(
        model=settings.effective_chat_model,
        api_key=settings.effective_chat_api_key,
        base_url=settings.effective_chat_base_url,
        temperature=0.1,
        max_tokens=settings.chat_max_tokens,
    )


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


def build_fact_proposal_generator(
    settings: Settings,
    *,
    chat: ChatPort,
) -> FactProposalGeneratorPort:
    return StructuredFactProposalGenerator(
        chat,
        max_completion_tokens=settings.chat_max_tokens,
    )


def build_retriever(_settings: Settings) -> RetrievePort:
    return HybridRetrieverAdapter()


def build_web_search(_settings: Settings) -> WebSearchPort:
    return DuckDuckGoAdapter()


def build_workflow_runtime(
    settings: Settings,
    *,
    trace: TracePort | None = None,
    metrics: MetricsPort | None = None,
    planner: EvidencePlannerPort | None = None,
    tools: CaseAssessmentToolPort | None = None,
) -> WorkflowRuntimePort:
    return LangGraphWorkflowRuntime(
        settings.langgraph_checkpoint_db_path,
        checkpoint_backend=("postgres" if settings.storage_backend == "postgres" else "sqlite"),
        database_url=settings.database_url,
        trace=trace,
        planner=planner or build_evidence_planner(settings),
        tools=tools,
        metrics=metrics,
        model_name=settings.effective_chat_model,
        input_cost_per_1m_tokens=settings.llm_input_cost_per_1m_tokens,
        output_cost_per_1m_tokens=settings.llm_output_cost_per_1m_tokens,
        budget=AgentBudget(
            max_loop_count=settings.agent_max_loop_count,
            max_tool_calls=settings.agent_max_tool_calls,
            max_tokens=settings.agent_max_tokens,
        ),
    )


def build_evidence_planner(
    settings: Settings,
    *,
    model: BaseChatModel | None = None,
) -> EvidencePlannerPort:
    if settings.agent_planner_backend == "deterministic":
        return DeterministicEvidencePlanner()
    return LangChainEvidencePlanner(model or build_agent_model(settings))


def build_risk_profile(
    _settings: Settings,
    *,
    trace: TracePort | None = None,
) -> RiskProfilePort:
    return HttpRiskProfileClient(
        base_url=_settings.risk_profile_api_base,
        api_key=_settings.risk_profile_api_key,
        timeout_seconds=_settings.risk_profile_timeout_seconds,
        trace=trace,
    )


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
    return PrometheusMetricsAdapter(cost_currency=settings.llm_cost_currency)


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
        recall_policy=MemoryRecallPolicy(
            semantic_weight=settings.memory_recall_semantic_weight,
            confidence_weight=settings.memory_recall_confidence_weight,
            salience_weight=settings.memory_recall_salience_weight,
            freshness_weight=settings.memory_recall_freshness_weight,
            min_semantic_score=settings.memory_recall_min_semantic_score,
            min_final_score=settings.memory_recall_min_final_score,
            freshness_half_life_days=(settings.memory_recall_freshness_half_life_days),
        ),
        recall_candidate_multiplier=settings.memory_recall_candidate_multiplier,
    )


def build_profile_store(settings: Settings, *, task_repo: TaskRepoPort) -> ProfileStorePort | None:
    """构造 L3 画像存储；禁用记忆 / 画像时返回 None。复用 task 连接池。"""
    if not (settings.memory_enabled and settings.memory_profile_enabled):
        return None
    if isinstance(task_repo, SqliteTaskRepo):
        return SqliteProfileStore(task_repo._pool)  # noqa: SLF001 — 同包复用连接池
    return SqliteProfileStore(build_sqlite_pool(settings))


def build_memory_settings_store(
    settings: Settings, *, task_repo: TaskRepoPort
) -> MemorySettingsStorePort | None:
    """构造每用户记忆开关存储；记忆全局禁用时返回 None。复用 task 连接池。

    与画像 / 摘要不同：开关不受 ``memory_summary_enabled`` 等子开关影响，
    只要 ``memory_enabled`` 即启用——用户始终可读写自己的偏好。
    """
    if not settings.memory_enabled:
        return None
    if isinstance(task_repo, SqliteTaskRepo):
        return SqliteMemorySettingsStore(task_repo._pool)  # noqa: SLF001 — 同包复用连接池
    return SqliteMemorySettingsStore(build_sqlite_pool(settings))


def build_summary_store(settings: Settings, *, task_repo: TaskRepoPort) -> SummaryStorePort | None:
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


def build_research(
    _settings: Settings,
    *,
    retriever: RetrievePort,
    web_search: WebSearchPort,
    chat: ChatPort,
    trace: TracePort | None = None,
) -> ResearchPort:
    """构造显式 LangGraph Deep Research。"""
    return LangGraphResearchAdapter(
        retriever=retriever,
        web_search=web_search,
        chat=chat,
        trace=trace,
    )


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
    "build_assessment_repo",
    "build_agent_run_repo",
    "build_audit_log",
    "build_auth",
    "build_case_repo",
    "build_case_fact_repo",
    "build_chat",
    "build_claim_support_verifier",
    "build_consolidation_state_store",
    "build_consolidation_worker",
    "build_document_loader",
    "build_document_parser",
    "build_document_repo",
    "build_embedder",
    "build_evidence_qa_generator",
    "build_evidence_chunker",
    "build_evidence_index",
    "build_fact_store",
    "build_job_dispatcher",
    "build_kb_repo",
    "build_memory",
    "build_memory_scheduler",
    "build_object_store",
    "build_policy_rule_repo",
    "build_profile_store",
    "build_research",
    "build_readiness",
    "build_retriever",
    "build_risk_profile",
    "build_sqlite_pool",
    "build_sqlalchemy_database",
    "build_summary_store",
    "build_task_repo",
    "build_trace",
    "build_user_repo",
    "build_web_search",
    "build_workflow_runtime",
    "build_workspace_repo",
]
