"""app/factories.py 的轻量级烟雾测试：每个 build_* 返回正确 Port 类型。

不真实调用任何远端服务；只验装配链路与类型契约。
"""

from __future__ import annotations

import tempfile

import pytest

from app.factories import (
    build_agent_run_repo,
    build_assessment_repo,
    build_audit_log,
    build_auth,
    build_case_fact_repo,
    build_case_repo,
    build_chat,
    build_claim_support_verifier,
    build_document_parser,
    build_document_repo,
    build_embedder,
    build_evidence_index,
    build_evidence_qa_generator,
    build_fact_proposal_generator,
    build_object_store,
    build_policy_rule_repo,
    build_retriever,
    build_risk_profile,
    build_sqlalchemy_database,
    build_sqlite_pool,
    build_task_repo,
    build_trace,
    build_user_repo,
    build_web_search,
    build_workflow_runtime,
    build_workspace_repo,
)
from config import Settings
from domain.ports import (
    AgentRunRepoPort,
    AssessmentRepoPort,
    AuditLogPort,
    AuthPort,
    CaseFactRepoPort,
    CaseRepoPort,
    ChatPort,
    ClaimSupportVerifierPort,
    DocumentParserPort,
    DocumentRepoPort,
    EmbedPort,
    EvidenceQAGeneratorPort,
    FactProposalGeneratorPort,
    ObjectStorePort,
    PolicyRuleRepoPort,
    RetrievePort,
    RiskProfilePort,
    TaskRepoPort,
    TracePort,
    UserRepoPort,
    WebSearchPort,
    WorkflowRuntimePort,
    WorkspaceRepoPort,
)
from infra.object_store import S3ObjectStore
from infra.storage.sqlalchemy import (
    SqlAlchemyAgentRunRepo,
    SqlAlchemyAssessmentRepo,
    SqlAlchemyCaseFactRepo,
    SqlAlchemyCaseRepo,
    SqlAlchemyDocumentRepo,
    SqlAlchemyEvidenceIndex,
    SqlAlchemyPolicyRuleRepo,
    SqlAlchemyWorkspaceRepo,
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
    def test_agent_run_repo_satisfies_port(self, settings: Settings) -> None:
        pool = build_sqlite_pool(settings)
        assert isinstance(build_agent_run_repo(settings, pool=pool), AgentRunRepoPort)

    def test_assessment_repo_satisfies_port(self, settings: Settings) -> None:
        pool = build_sqlite_pool(settings)
        assert isinstance(build_assessment_repo(settings, pool=pool), AssessmentRepoPort)

    def test_user_repo_satisfies_port(self, settings: Settings) -> None:
        pool = build_sqlite_pool(settings)
        assert isinstance(build_user_repo(settings, pool=pool), UserRepoPort)

    def test_task_repo_satisfies_port(self, settings: Settings) -> None:
        pool = build_sqlite_pool(settings)
        assert isinstance(build_task_repo(settings, pool=pool), TaskRepoPort)

    def test_workspace_repo_satisfies_port(self, settings: Settings) -> None:
        pool = build_sqlite_pool(settings)
        assert isinstance(build_workspace_repo(settings, pool=pool), WorkspaceRepoPort)

    def test_case_repo_satisfies_port(self, settings: Settings) -> None:
        pool = build_sqlite_pool(settings)
        assert isinstance(build_case_repo(settings, pool=pool), CaseRepoPort)

    def test_case_fact_repo_satisfies_port(self, settings: Settings) -> None:
        pool = build_sqlite_pool(settings)
        assert isinstance(build_case_fact_repo(settings, pool=pool), CaseFactRepoPort)

    def test_document_repo_satisfies_port(self, settings: Settings) -> None:
        pool = build_sqlite_pool(settings)
        assert isinstance(build_document_repo(settings, pool=pool), DocumentRepoPort)

    def test_document_parser_satisfies_port(self, settings: Settings) -> None:
        assert isinstance(build_document_parser(settings), DocumentParserPort)

    def test_object_store_satisfies_port(self, settings: Settings) -> None:
        assert isinstance(build_object_store(settings), ObjectStorePort)

    def test_s3_object_store_profile_satisfies_port(self, settings: Settings) -> None:
        s3_settings = settings.model_copy(
            update={
                "object_store_backend": "s3",
                "s3_endpoint_url": "http://127.0.0.1:9000",
                "s3_access_key_id": "riskpilot",
                "s3_secret_access_key": "test-only-secret",
            }
        )
        store = build_object_store(s3_settings)
        assert isinstance(store, S3ObjectStore)
        assert isinstance(store, ObjectStorePort)

    def test_policy_rule_repo_satisfies_port(self, settings: Settings) -> None:
        pool = build_sqlite_pool(settings)
        assert isinstance(build_policy_rule_repo(settings, pool=pool), PolicyRuleRepoPort)

    def test_audit_log_satisfies_port(self, settings: Settings) -> None:
        pool = build_sqlite_pool(settings)
        assert isinstance(build_audit_log(settings, pool=pool), AuditLogPort)

    def test_postgres_profile_selects_sqlalchemy_core_repositories(
        self,
        settings: Settings,
    ) -> None:
        postgres_settings = settings.model_copy(
            update={
                "storage_backend": "postgres",
                "vector_backend": "pgvector",
                "embedding_dimensions": 2048,
                "database_url": "sqlite://",
            }
        )
        database = build_sqlalchemy_database(postgres_settings)
        try:
            assert isinstance(
                build_workspace_repo(postgres_settings, database=database),
                SqlAlchemyWorkspaceRepo,
            )
            assert isinstance(
                build_case_repo(postgres_settings, database=database),
                SqlAlchemyCaseRepo,
            )
            assert isinstance(
                build_document_repo(postgres_settings, database=database),
                SqlAlchemyDocumentRepo,
            )
            assert isinstance(
                build_case_fact_repo(postgres_settings, database=database),
                SqlAlchemyCaseFactRepo,
            )
            assert isinstance(
                build_policy_rule_repo(postgres_settings, database=database),
                SqlAlchemyPolicyRuleRepo,
            )
            assert isinstance(
                build_assessment_repo(postgres_settings, database=database),
                SqlAlchemyAssessmentRepo,
            )
            assert isinstance(
                build_agent_run_repo(postgres_settings, database=database),
                SqlAlchemyAgentRunRepo,
            )
            assert isinstance(
                build_evidence_index(postgres_settings, database=database),
                SqlAlchemyEvidenceIndex,
            )
        finally:
            database.dispose()


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

    def test_evidence_qa_components_satisfy_ports(self, settings: Settings) -> None:
        chat = build_chat(settings)
        assert isinstance(
            build_evidence_qa_generator(settings, chat=chat),
            EvidenceQAGeneratorPort,
        )
        assert isinstance(
            build_fact_proposal_generator(settings, chat=chat),
            FactProposalGeneratorPort,
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

    def test_risk_profile_satisfies_port(self, settings: Settings) -> None:
        assert isinstance(build_risk_profile(settings), RiskProfilePort)

    def test_trace_defaults_to_noop_port(self, settings: Settings) -> None:
        assert isinstance(build_trace(settings), TracePort)

    def test_workflow_runtime_satisfies_port(self, settings: Settings) -> None:
        settings = settings.model_copy(update={"langgraph_checkpoint_db_path": ":memory:"})
        assert isinstance(build_workflow_runtime(settings), WorkflowRuntimePort)
