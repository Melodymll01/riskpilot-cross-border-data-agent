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
    build_claim_support_verifier,
    build_document_parser,
    build_document_repo,
    build_embedder,
    build_evidence_qa_generator,
    build_object_store,
    build_retriever,
    build_sqlite_pool,
    build_task_repo,
    build_trace,
    build_user_repo,
    build_web_search,
)
from config import Settings
from domain.ports import (
    AuditLogPort,
    AuthPort,
    ChatPort,
    ClaimSupportVerifierPort,
    DocumentParserPort,
    DocumentRepoPort,
    EmbedPort,
    EvidenceQAGeneratorPort,
    ObjectStorePort,
    RetrievePort,
    TaskRepoPort,
    TracePort,
    UserRepoPort,
    WebSearchPort,
)


@pytest.fixture
def settings() -> Settings:
    """临时 SQLite 路径的 Settings 实例，避免污染生产 db。"""
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
    tmp.close()
    return Settings(
        sqlite_db_path=tmp.name,
        object_store_dir=f"{tmp.name}.objects",
    )


class TestStorageFactories:
    def test_user_repo_satisfies_port(self, settings: Settings) -> None:
        pool = build_sqlite_pool(settings)
        assert isinstance(build_user_repo(settings, pool=pool), UserRepoPort)

    def test_task_repo_satisfies_port(self, settings: Settings) -> None:
        pool = build_sqlite_pool(settings)
        assert isinstance(build_task_repo(settings, pool=pool), TaskRepoPort)

    def test_document_repo_satisfies_port(self, settings: Settings) -> None:
        pool = build_sqlite_pool(settings)
        assert isinstance(build_document_repo(settings, pool=pool), DocumentRepoPort)

    def test_document_parser_satisfies_port(self, settings: Settings) -> None:
        assert isinstance(build_document_parser(settings), DocumentParserPort)

    def test_object_store_satisfies_port(self, settings: Settings) -> None:
        assert isinstance(build_object_store(settings), ObjectStorePort)

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

    def test_agent_model_uses_real_streaming_without_hidden_retries(
        self, settings: Settings
    ) -> None:
        from app.factories import build_agent_model

        model = build_agent_model(settings)
        assert model.streaming is True
        assert model.stream_usage is True
        assert model.max_retries == 0
        assert model.request_timeout == settings.conversation_model_call_timeout_seconds

    def test_evidence_qa_components_satisfy_ports(self, settings: Settings) -> None:
        chat = build_chat(settings)
        assert isinstance(
            build_evidence_qa_generator(settings, chat=chat),
            EvidenceQAGeneratorPort,
        )
        assert isinstance(
            build_claim_support_verifier(settings, chat=chat),
            ClaimSupportVerifierPort,
        )

    def test_embedder_satisfies_port(self, settings: Settings) -> None:
        assert isinstance(build_embedder(settings), EmbedPort)

    def test_retriever_satisfies_port(self, settings: Settings) -> None:
        assert isinstance(build_retriever(settings), RetrievePort)

    def test_web_search_satisfies_port(self, settings: Settings) -> None:
        assert isinstance(build_web_search(settings), WebSearchPort)

    def test_trace_defaults_to_noop_port(self, settings: Settings) -> None:
        assert isinstance(build_trace(settings), TracePort)
