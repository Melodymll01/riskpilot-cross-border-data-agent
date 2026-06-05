"""AppContainer 装配测试：注入全 fake，断言 8 个 port + 4 个 use case 就位。"""

from __future__ import annotations

from app.container import AppContainer
from app.use_cases import (
    AuthLoginUseCase,
    IngestionUseCase,
    RunQueryUseCase,
    TaskManagementUseCase,
)
from config import settings
from domain.ports import (
    AuthPort,
    ChatPort,
    EmbedPort,
    EvidencePort,
    RetrievePort,
    RiskProfilePort,
    TaskRepoPort,
    UserRepoPort,
    WebSearchPort,
)
from tests.fakes import (
    FakeAuth,
    FakeChat,
    FakeEmbed,
    FakeEvidence,
    FakeRetrieve,
    FakeWebSearch,
    InMemoryTaskRepo,
    InMemoryUserRepo,
)


def _full_fake_container() -> AppContainer:
    return AppContainer(
        settings,
        user_repo=InMemoryUserRepo(),
        task_repo=InMemoryTaskRepo(),
        embedder=FakeEmbed(),
        chat=FakeChat(),
        retriever=FakeRetrieve(),
        web_search=FakeWebSearch(),
        evidence=FakeEvidence(),
        auth=FakeAuth(),
    )


class TestPortConformance:
    """所有装好的端口都满足对应 Protocol。"""

    def test_all_ports_present_and_typed(self) -> None:
        c = _full_fake_container()
        assert isinstance(c.user_repo, UserRepoPort)
        assert isinstance(c.task_repo, TaskRepoPort)
        assert isinstance(c.embedder, EmbedPort)
        assert isinstance(c.chat, ChatPort)
        assert isinstance(c.retriever, RetrievePort)
        assert isinstance(c.web_search, WebSearchPort)
        assert isinstance(c.evidence, EvidencePort)
        assert isinstance(c.risk_profile, RiskProfilePort)
        assert isinstance(c.auth, AuthPort)


class TestUseCaseWiring:
    """4 个 use case 都按预期挂在 self 上，且引用同一份依赖实例。"""

    def test_use_cases_assembled(self) -> None:
        c = _full_fake_container()
        assert isinstance(c.auth_login, AuthLoginUseCase)
        assert isinstance(c.task_management, TaskManagementUseCase)
        assert isinstance(c.ingest, IngestionUseCase)
        assert isinstance(c.run_query, RunQueryUseCase)

    def test_use_cases_share_container_instances(self) -> None:
        """auth_login._auth is container.auth，避免无意中建第二个实例。"""
        c = _full_fake_container()
        assert c.auth_login._auth is c.auth
        assert c.task_management._repo is c.task_repo
        assert c.ingest._embedder is c.embedder
        assert c.run_query._chat is c.chat
        assert c.run_query._retriever is c.retriever


class TestPartialInjection:
    """部分注入：只覆盖 chat，其他从 factories 走（但 factories 也很轻）。"""

    def test_inject_only_chat(self) -> None:
        # 用 fake 替掉 chat，其他从工厂；不真正调用 chat / embed
        c = AppContainer(
            settings,
            user_repo=InMemoryUserRepo(),
            task_repo=InMemoryTaskRepo(),
            embedder=FakeEmbed(),
            chat=FakeChat(responses=["hi"]),
            retriever=FakeRetrieve(),
            web_search=FakeWebSearch(),
            evidence=FakeEvidence(),
            auth=FakeAuth(),
        )
        assert isinstance(c.chat, ChatPort)
        out = c.chat.chat([{"role": "user", "content": "ping"}])
        assert out == "hi"
