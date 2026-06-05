"""``AppContainer``：DI 装配中心。

职责：
- 一次性把 8 个 Port 装好，所有 use case 共享同一组实例（保证 SQLite 单连接池）
- 支持"全工厂"模式（生产）与"全注入"模式（测试）混用
- 同步装配 4 个 use case，挂在 self 上

用法：
    >>> from config import settings
    >>> container = AppContainer(settings)
    >>> container.auth_login.login_anonymous()

测试：
    >>> container = AppContainer(
    ...     settings, auth=FakeAuth(), task_repo=InMemoryTaskRepo(), ...
    ... )
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.agent import ComplianceCopilotAgent, register_default_tools
from app.factories import (
    build_auth,
    build_chat,
    build_embedder,
    build_evidence,
    build_retriever,
    build_risk_profile,
    build_sqlite_pool,
    build_task_repo,
    build_user_repo,
    build_web_search,
)
from app.use_cases import (
    AuthLoginUseCase,
    IngestionUseCase,
    RunQueryUseCase,
    TaskManagementUseCase,
)
from app.use_cases.run_copilot import RunCopilotUseCase

if TYPE_CHECKING:
    from config import Settings
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


class AppContainer:
    """8 个 Port + 4 个 use case 的中央配电盘。"""

    def __init__(
        self,
        settings: Settings,
        *,
        user_repo: UserRepoPort | None = None,
        task_repo: TaskRepoPort | None = None,
        embedder: EmbedPort | None = None,
        chat: ChatPort | None = None,
        retriever: RetrievePort | None = None,
        web_search: WebSearchPort | None = None,
        evidence: EvidencePort | None = None,
        risk_profile: RiskProfilePort | None = None,
        auth: AuthPort | None = None,
    ) -> None:
        self.settings = settings

        # ── 存储层：SQLite 单连接池给两个 repo 共享 ─────────────────────
        pool = (
            build_sqlite_pool(settings)
            if user_repo is None or task_repo is None
            else None
        )
        self.user_repo: UserRepoPort = user_repo or build_user_repo(
            settings, pool=pool
        )
        self.task_repo: TaskRepoPort = task_repo or build_task_repo(
            settings, pool=pool
        )

        # ── LLM / 检索 / 外部 ─────────────────────────────────────────
        self.embedder: EmbedPort = embedder or build_embedder(settings)
        self.chat: ChatPort = chat or build_chat(settings)
        self.retriever: RetrievePort = retriever or build_retriever(settings)
        self.web_search: WebSearchPort = web_search or build_web_search(settings)
        self.evidence: EvidencePort = evidence or build_evidence(settings)
        self.risk_profile: RiskProfilePort = risk_profile or build_risk_profile(
            settings
        )

        # ── 鉴权（依赖 user_repo） ────────────────────────────────────
        self.auth: AuthPort = auth or build_auth(settings, self.user_repo)

        # ── use case 装配 ─────────────────────────────────────────────
        self.auth_login = AuthLoginUseCase(self.auth)
        self.task_management = TaskManagementUseCase(self.task_repo)
        self.ingest = IngestionUseCase(self.embedder)
        self.run_query = RunQueryUseCase(retriever=self.retriever, chat=self.chat)

        # ── Agent 层（Step 009 PR-5b）─────────────────────────────────
        # 工具注册表必须晚于所有 port 初始化，因为 handler 闭包持有 self.* 引用
        self.tool_registry = register_default_tools(self)
        self.copilot_agent = ComplianceCopilotAgent(
            chat=self.chat,
            task_repo=self.task_repo,
            tool_registry=self.tool_registry,
        )
        self.run_copilot = RunCopilotUseCase(
            agent=self.copilot_agent,
            task_management=self.task_management,
            risk_profile=self.risk_profile,
        )


__all__ = ["AppContainer"]
