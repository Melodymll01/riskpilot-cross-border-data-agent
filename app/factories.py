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
    DocumentLoaderPort,
    EmbedPort,
    EvidencePort,
    KbDocumentRepoPort,
    MemoryPort,
    ResearchPort,
    RetrievePort,
    RiskProfilePort,
    TaskRepoPort,
    UserRepoPort,
    WebSearchPort,
)
from infra.audit import SqliteAuditLogRepo
from infra.auth import AnonymousProvider, AuthService, GitHubOAuthProvider, JwtIssuer
from infra.chat import OpenAIChatAdapter
from infra.evidence import MockEvidenceClient
from infra.kb import ChromaKbRepo, UnifiedLoaderAdapter
from infra.memory import TaskBackedMemory
from infra.research import AgenticResearchAdapter
from infra.risk_profile import StubRiskProfileService
from infra.search import EmbedderAdapter, HybridRetrieverAdapter
from infra.storage import SqliteTaskRepo, SqliteUserRepo
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


def build_memory(settings: Settings, *, task_repo: TaskRepoPort) -> MemoryPort | None:
    """构造 ``MemoryPort``（S-030a 仅 L1）；禁用时返回 None。

    None 是合法状态：表示"记忆关闭"，装配器据此退回无状态旧行为。
    """
    if not settings.memory_enabled:
        return None
    return TaskBackedMemory(task_repo)


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
    "build_document_loader",
    "build_embedder",
    "build_evidence",
    "build_kb_repo",
    "build_memory",
    "build_research",
    "build_retriever",
    "build_risk_profile",
    "build_sqlite_pool",
    "build_task_repo",
    "build_user_repo",
    "build_web_search",
]
