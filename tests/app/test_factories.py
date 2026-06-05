"""app/factories.py 的轻量级烟雾测试：每个 build_* 返回正确 Port 类型。

不真实调用任何远端服务；只验装配链路与类型契约。
"""

from __future__ import annotations

import tempfile

import pytest

from app.factories import (
    build_audit_log,
    build_auth,
    build_chat,
    build_embedder,
    build_evidence,
    build_retriever,
    build_sqlite_pool,
    build_task_repo,
    build_user_repo,
    build_web_search,
)
from config import Settings
from domain.ports import (
    AuditLogPort,
    AuthPort,
    ChatPort,
    EmbedPort,
    EvidencePort,
    RetrievePort,
    TaskRepoPort,
    UserRepoPort,
    WebSearchPort,
)


@pytest.fixture
def settings() -> Settings:
    """临时 SQLite 路径的 Settings 实例，避免污染生产 db。"""
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
    tmp.close()
    return Settings(sqlite_db_path=tmp.name)


class TestStorageFactories:
    def test_user_repo_satisfies_port(self, settings: Settings) -> None:
        pool = build_sqlite_pool(settings)
        assert isinstance(build_user_repo(settings, pool=pool), UserRepoPort)

    def test_task_repo_satisfies_port(self, settings: Settings) -> None:
        pool = build_sqlite_pool(settings)
        assert isinstance(build_task_repo(settings, pool=pool), TaskRepoPort)

    def test_audit_log_satisfies_port(self, settings: Settings) -> None:
        pool = build_sqlite_pool(settings)
        assert isinstance(build_audit_log(settings, pool=pool), AuditLogPort)


class TestAuthFactory:
    def test_auth_satisfies_port(self, settings: Settings) -> None:
        pool = build_sqlite_pool(settings)
        user_repo = build_user_repo(settings, pool=pool)
        auth = build_auth(settings, user_repo)
        assert isinstance(auth, AuthPort)

    def test_anonymous_login_round_trip(self, settings: Settings) -> None:
        pool = build_sqlite_pool(settings)
        user_repo = build_user_repo(settings, pool=pool)
        auth = build_auth(settings, user_repo)
        user = auth.create_anonymous()
        token = auth.issue_jwt(user.user_id)
        assert auth.verify_jwt(token) == user.user_id


class TestExternalFactories:
    """LLM/检索/搜索：只测构造，不真调远端。"""

    def test_chat_satisfies_port(self, settings: Settings) -> None:
        assert isinstance(build_chat(settings), ChatPort)

    def test_embedder_satisfies_port(self, settings: Settings) -> None:
        assert isinstance(build_embedder(settings), EmbedPort)

    def test_retriever_satisfies_port(self, settings: Settings) -> None:
        assert isinstance(build_retriever(settings), RetrievePort)

    def test_web_search_satisfies_port(self, settings: Settings) -> None:
        assert isinstance(build_web_search(settings), WebSearchPort)

    def test_evidence_satisfies_port(self, settings: Settings) -> None:
        assert isinstance(build_evidence(settings), EvidencePort)
