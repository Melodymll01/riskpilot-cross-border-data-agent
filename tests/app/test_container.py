"""AppContainer 装配测试：注入全 fake，断言核心 Port 与 Use Case 就位。"""

from __future__ import annotations

from app.container import AppContainer
from app.use_cases import (
    AssessmentRunUseCase,
    AuthLoginUseCase,
    CaseManagementUseCase,
    EvidenceQAUseCase,
    KbManagementUseCase,
    TaskManagementUseCase,
    WorkspaceManagementUseCase,
)
from config import settings
from domain.ports import (
    AgentRunRepoPort,
    AssessmentRepoPort,
    AuditLogPort,
    AuthPort,
    CaseFactRepoPort,
    CaseRepoPort,
    ChatPort,
    ClaimSupportVerifierPort,
    DocumentLoaderPort,
    DocumentRepoPort,
    EmbedPort,
    EvidenceChunkerPort,
    EvidenceIndexPort,
    EvidenceQAGeneratorPort,
    FactProposalGeneratorPort,
    KbDocumentRepoPort,
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
from infra.storage.sqlalchemy import (
    Base,
    SqlAlchemyAgentRunRepo,
    SqlAlchemyAssessmentRepo,
    SqlAlchemyCaseFactRepo,
    SqlAlchemyCaseRepo,
    SqlAlchemyDocumentRepo,
    SqlAlchemyEvidenceIndex,
    SqlAlchemyPolicyRuleRepo,
    SqlAlchemyWorkspaceRepo,
)
from infra.workflows import LangGraphWorkflowRuntime
from tests.fakes import (
    FakeAuditLogRepo,
    FakeAuth,
    FakeChat,
    FakeClaimSupportVerifier,
    FakeDocumentLoader,
    FakeDocumentParser,
    FakeEmbed,
    FakeEvidenceChunker,
    FakeEvidenceIndex,
    FakeEvidenceQAGenerator,
    FakeFactProposalGenerator,
    FakeKbRepo,
    FakeObjectStore,
    FakeReadiness,
    FakeRetrieve,
    FakeRiskProfile,
    FakeTrace,
    FakeVisualEmbedder,
    FakeVisualIndex,
    FakeWebSearch,
    InMemoryAgentRunRepo,
    InMemoryAssessmentRepo,
    InMemoryCaseFactRepo,
    InMemoryCaseRepo,
    InMemoryDocumentRepo,
    InMemoryPolicyRuleRepo,
    InMemoryTaskRepo,
    InMemoryUserRepo,
    InMemoryWorkspaceRepo,
    final_answer_model,
)


def _full_fake_container() -> AppContainer:
    document_repo = InMemoryDocumentRepo()
    case_repo = InMemoryCaseRepo()
    trace = FakeTrace()
    return AppContainer(
        settings,
        agent_run_repo=InMemoryAgentRunRepo(),
        assessment_repo=InMemoryAssessmentRepo(case_repo),
        user_repo=InMemoryUserRepo(),
        task_repo=InMemoryTaskRepo(),
        workspace_repo=InMemoryWorkspaceRepo(),
        case_repo=case_repo,
        case_fact_repo=InMemoryCaseFactRepo(),
        policy_rule_repo=InMemoryPolicyRuleRepo(),
        document_repo=document_repo,
        object_store=FakeObjectStore(),
        document_parser=FakeDocumentParser(),
        evidence_chunker=FakeEvidenceChunker(),
        evidence_index=FakeEvidenceIndex(document_repo),
        workflow_runtime=LangGraphWorkflowRuntime(":memory:"),
        audit_log=FakeAuditLogRepo(),
        embedder=FakeEmbed(),
        chat=FakeChat(),
        evidence_qa_generator=FakeEvidenceQAGenerator(),
        claim_support_verifier=FakeClaimSupportVerifier(),
        fact_proposal_generator=FakeFactProposalGenerator(),
        retriever=FakeRetrieve(),
        web_search=FakeWebSearch(),
        risk_profile=FakeRiskProfile(),
        readiness=FakeReadiness(),
        trace=trace,
        kb_repo=FakeKbRepo(),
        document_loader=FakeDocumentLoader(),
        auth=FakeAuth(),
        agent_model=final_answer_model(),
        visual_index=FakeVisualIndex(),
        visual_embedder=FakeVisualEmbedder(),
    )


class TestPortConformance:
    """所有装好的端口都满足对应 Protocol。"""

    def test_all_ports_present_and_typed(self) -> None:
        c = _full_fake_container()
        assert isinstance(c.agent_run_repo, AgentRunRepoPort)
        assert isinstance(c.assessment_repo, AssessmentRepoPort)
        assert isinstance(c.user_repo, UserRepoPort)
        assert isinstance(c.task_repo, TaskRepoPort)
        assert isinstance(c.workspace_repo, WorkspaceRepoPort)
        assert isinstance(c.case_repo, CaseRepoPort)
        assert isinstance(c.case_fact_repo, CaseFactRepoPort)
        assert isinstance(c.policy_rule_repo, PolicyRuleRepoPort)
        assert isinstance(c.document_repo, DocumentRepoPort)
        assert isinstance(c.object_store, ObjectStorePort)
        assert isinstance(c.evidence_chunker, EvidenceChunkerPort)
        assert isinstance(c.evidence_index, EvidenceIndexPort)
        assert isinstance(c.audit_log, AuditLogPort)
        assert isinstance(c.embedder, EmbedPort)
        assert isinstance(c.chat, ChatPort)
        assert isinstance(c.evidence_qa_generator, EvidenceQAGeneratorPort)
        assert isinstance(c.claim_support_verifier, ClaimSupportVerifierPort)
        assert isinstance(c.fact_proposal_generator, FactProposalGeneratorPort)
        assert isinstance(c.retriever, RetrievePort)
        assert isinstance(c.web_search, WebSearchPort)
        assert isinstance(c.workflow_runtime, WorkflowRuntimePort)
        assert isinstance(c.risk_profile, RiskProfilePort)
        assert isinstance(c.trace, TracePort)
        assert isinstance(c.kb_repo, KbDocumentRepoPort)
        assert isinstance(c.document_loader, DocumentLoaderPort)
        assert isinstance(c.auth, AuthPort)


class TestUseCaseWiring:
    """核心 use case 都按预期挂在 self 上，且引用同一份依赖实例。"""

    def test_use_cases_assembled(self) -> None:
        c = _full_fake_container()
        assert isinstance(c.auth_login, AuthLoginUseCase)
        assert isinstance(c.task_management, TaskManagementUseCase)
        assert isinstance(c.workspace_management, WorkspaceManagementUseCase)
        assert isinstance(c.case_management, CaseManagementUseCase)
        assert isinstance(c.kb_management, KbManagementUseCase)
        assert isinstance(c.assessment_runs, AssessmentRunUseCase)
        assert isinstance(c.evidence_qa, EvidenceQAUseCase)

    def test_use_cases_share_container_instances(self) -> None:
        """auth_login._auth is container.auth，避免无意中建第二个实例。"""
        c = _full_fake_container()
        assert c.auth_login._auth is c.auth
        assert c.task_management._repo is c.task_repo
        assert c.workspace_management._repo is c.workspace_repo
        assert c.case_management._case_repo is c.case_repo
        assert c.case_management._workspace_repo is c.workspace_repo
        assert c.assessment_runs._runs is c.agent_run_repo
        assert c.assessment_runs._runtime is c.workflow_runtime
        assert c.assessment_runs._assessments is c.assessment_management
        assert c.evidence_qa._retriever is c.retriever
        assert c.evidence_qa._evidence_index is c.evidence_index
        assert c.evidence_qa._documents is c.document_repo
        assert c.evidence_qa._generator is c.evidence_qa_generator
        assert c.evidence_qa._support_verifier is c.claim_support_verifier
        assert c.kb_management._repo is c.kb_repo
        assert c.kb_management._loader is c.document_loader
        assert c.kb_management._embedder is c.embedder
        assert c.copilot_agent._trace is c.trace


class TestPartialInjection:
    """部分注入：只覆盖 chat，其他从 factories 走（但 factories 也很轻）。"""

    def test_inject_only_chat(self) -> None:
        # 用 fake 替掉 chat，其他从工厂；不真正调用 chat / embed
        document_repo = InMemoryDocumentRepo()
        case_repo = InMemoryCaseRepo()
        c = AppContainer(
            settings,
            agent_run_repo=InMemoryAgentRunRepo(),
            assessment_repo=InMemoryAssessmentRepo(case_repo),
            user_repo=InMemoryUserRepo(),
            task_repo=InMemoryTaskRepo(),
            workspace_repo=InMemoryWorkspaceRepo(),
            case_repo=case_repo,
            case_fact_repo=InMemoryCaseFactRepo(),
            policy_rule_repo=InMemoryPolicyRuleRepo(),
            document_repo=document_repo,
            object_store=FakeObjectStore(),
            document_parser=FakeDocumentParser(),
            evidence_chunker=FakeEvidenceChunker(),
            evidence_index=FakeEvidenceIndex(document_repo),
            workflow_runtime=LangGraphWorkflowRuntime(":memory:"),
            embedder=FakeEmbed(),
            chat=FakeChat(responses=["hi"]),
            evidence_qa_generator=FakeEvidenceQAGenerator(),
            claim_support_verifier=FakeClaimSupportVerifier(),
            retriever=FakeRetrieve(),
            web_search=FakeWebSearch(),
            risk_profile=FakeRiskProfile(),
            kb_repo=FakeKbRepo(),
            document_loader=FakeDocumentLoader(),
            auth=FakeAuth(),
            agent_model=final_answer_model(),
            visual_index=FakeVisualIndex(),
            visual_embedder=FakeVisualEmbedder(),
        )
        assert isinstance(c.chat, ChatPort)
        out = c.chat.chat([{"role": "user", "content": "ping"}])
        assert out == "hi"


class TestPostgresProfile:
    def test_selects_shared_sqlalchemy_core_adapters(self) -> None:
        postgres_settings = settings.model_copy(
            update={
                "storage_backend": "postgres",
                "vector_backend": "pgvector",
                "embedding_dimensions": 2048,
                "database_url": "sqlite://",
            }
        )
        container = AppContainer(
            postgres_settings,
            user_repo=InMemoryUserRepo(),
            task_repo=InMemoryTaskRepo(),
            audit_log=FakeAuditLogRepo(),
            object_store=FakeObjectStore(),
            document_parser=FakeDocumentParser(),
            evidence_chunker=FakeEvidenceChunker(),
            workflow_runtime=LangGraphWorkflowRuntime(":memory:"),
            embedder=FakeEmbed(),
            chat=FakeChat(),
            evidence_qa_generator=FakeEvidenceQAGenerator(),
            claim_support_verifier=FakeClaimSupportVerifier(),
            fact_proposal_generator=FakeFactProposalGenerator(),
            retriever=FakeRetrieve(),
            web_search=FakeWebSearch(),
            risk_profile=FakeRiskProfile(),
            kb_repo=FakeKbRepo(),
            document_loader=FakeDocumentLoader(),
            auth=FakeAuth(),
            agent_model=final_answer_model(),
            visual_index=FakeVisualIndex(),
            visual_embedder=FakeVisualEmbedder(),
        )
        assert container.storage_database is not None
        Base.metadata.create_all(container.storage_database.engine)
        assert isinstance(container.workspace_repo, SqlAlchemyWorkspaceRepo)
        assert isinstance(container.case_repo, SqlAlchemyCaseRepo)
        assert isinstance(container.document_repo, SqlAlchemyDocumentRepo)
        assert isinstance(container.evidence_index, SqlAlchemyEvidenceIndex)
        assert isinstance(container.case_fact_repo, SqlAlchemyCaseFactRepo)
        assert isinstance(container.policy_rule_repo, SqlAlchemyPolicyRuleRepo)
        assert isinstance(container.assessment_repo, SqlAlchemyAssessmentRepo)
        assert isinstance(container.agent_run_repo, SqlAlchemyAgentRunRepo)
        assert container.readiness.check()["database"] is True
        container.storage_database.dispose()
