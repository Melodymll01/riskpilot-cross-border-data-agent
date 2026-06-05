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
    AuthPort,
    ChatPort,
    EmbedPort,
    EvidencePort,
    RetrievePort,
    TaskRepoPort,
    UserRepoPort,
    WebSearchPort,
)
from infra.auth import AnonymousProvider, AuthService, GitHubOAuthProvider, JwtIssuer
from infra.chat import OpenAIChatAdapter
from infra.evidence import MockEvidenceClient
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
    "build_auth",
    "build_chat",
    "build_embedder",
    "build_evidence",
    "build_retriever",
    "build_sqlite_pool",
    "build_task_repo",
    "build_user_repo",
    "build_web_search",
]
