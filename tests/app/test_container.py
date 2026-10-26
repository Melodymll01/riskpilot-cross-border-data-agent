"""AppContainer 装配测试：注入全 fake，断言核心 Port 与 Use Case 就位。"""

from __future__ import annotations

from app.container import AppContainer
from app.use_cases import (
    AuthLoginUseCase,
    EvidenceQAUseCase,
    KbManagementUseCase,
    TaskManagementUseCase,
)
from config import settings
from domain.ports import (
    AuditLogPort,
    AuthPort,
    ChatPort,
    ClaimSupportVerifierPort,
    DocumentLoaderPort,
    DocumentRepoPort,
    EmbedPort,
    EvidenceChunkerPort,
    EvidenceIndexPort,
    EvidenceQAGeneratorPort,
    KbDocumentRepoPort,
    ObjectStorePort,
    RetrievePort,
    TaskRepoPort,
    TracePort,
    UserRepoPort,
    WebSearchPort,
)
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
    FakeKbRepo,
    FakeObjectStore,
    FakeReadiness,
    FakeRetrieve,
    FakeTrace,
    FakeWebSearch,
    InMemoryDocumentRepo,
    InMemoryTaskRepo,
    InMemoryUserRepo,
    final_answer_model,
)


def _full_fake_container() -> AppContainer:
    document_repo = InMemoryDocumentRepo()
    trace = FakeTrace()
    return AppContainer(
        settings,
        user_repo=InMemoryUserRepo(),
        task_repo=InMemoryTaskRepo(),
        document_repo=document_repo,
        object_store=FakeObjectStore(),
        document_parser=FakeDocumentParser(),
        evidence_chunker=FakeEvidenceChunker(),
        evidence_index=FakeEvidenceIndex(document_repo),
        audit_log=FakeAuditLogRepo(),
        embedder=FakeEmbed(),
        chat=FakeChat(),
        evidence_qa_generator=FakeEvidenceQAGenerator(),
        claim_support_verifier=FakeClaimSupportVerifier(),
        retriever=FakeRetrieve(),
        web_search=FakeWebSearch(),
        readiness=FakeReadiness(),
        trace=trace,
        kb_repo=FakeKbRepo(),
        document_loader=FakeDocumentLoader(),
        auth=FakeAuth(),
        agent_model=final_answer_model(),
    )


class TestPortConformance:
    """所有装好的端口都满足对应 Protocol。"""

    def test_all_ports_present_and_typed(self) -> None:
        c = _full_fake_container()
        assert isinstance(c.user_repo, UserRepoPort)
        assert isinstance(c.task_repo, TaskRepoPort)
        assert isinstance(c.document_repo, DocumentRepoPort)
        assert isinstance(c.object_store, ObjectStorePort)
        assert isinstance(c.evidence_chunker, EvidenceChunkerPort)
        assert isinstance(c.evidence_index, EvidenceIndexPort)
        assert isinstance(c.audit_log, AuditLogPort)
        assert isinstance(c.embedder, EmbedPort)
        assert isinstance(c.chat, ChatPort)
        assert isinstance(c.evidence_qa_generator, EvidenceQAGeneratorPort)
        assert isinstance(c.claim_support_verifier, ClaimSupportVerifierPort)
        assert isinstance(c.retriever, RetrievePort)
        assert isinstance(c.web_search, WebSearchPort)
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
        assert isinstance(c.kb_management, KbManagementUseCase)
        assert isinstance(c.evidence_qa, EvidenceQAUseCase)

    def test_use_cases_share_container_instances(self) -> None:
        """auth_login._auth is container.auth，避免无意中建第二个实例。"""
        c = _full_fake_container()
        assert c.auth_login._auth is c.auth
        assert c.task_management._repo is c.task_repo
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
        c = AppContainer(
            settings,
            user_repo=InMemoryUserRepo(),
            task_repo=InMemoryTaskRepo(),
            document_repo=document_repo,
            object_store=FakeObjectStore(),
            document_parser=FakeDocumentParser(),
            evidence_chunker=FakeEvidenceChunker(),
            evidence_index=FakeEvidenceIndex(document_repo),
            embedder=FakeEmbed(),
            chat=FakeChat(responses=["hi"]),
            evidence_qa_generator=FakeEvidenceQAGenerator(),
            claim_support_verifier=FakeClaimSupportVerifier(),
            retriever=FakeRetrieve(),
            web_search=FakeWebSearch(),
            kb_repo=FakeKbRepo(),
            document_loader=FakeDocumentLoader(),
            auth=FakeAuth(),
            agent_model=final_answer_model(),
        )
        assert isinstance(c.chat, ChatPort)
        out = c.chat.chat([{"role": "user", "content": "ping"}])
        assert out == "hi"
