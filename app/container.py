"""``AppContainer``：DI 装配中心。

职责：
- 一次性把 10 个 Port 装好，所有 use case 共享同一组实例（保证 SQLite 单连接池）
- 支持"全工厂"模式（生产）与"全注入"模式（测试）混用
- 同步装配 5 个 use case，挂在 self 上

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
    build_audit_log,
    build_auth,
    build_chat,
    build_document_loader,
    build_embedder,
    build_evidence,
    build_kb_repo,
    build_research,
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
    KbManagementUseCase,
    RunQueryUseCase,
    TaskManagementUseCase,
)
from app.use_cases.run_copilot import RunCopilotUseCase

if TYPE_CHECKING:
    from config import Settings
    from domain.ports import (
        AuditLogPort,
        AuthPort,
        ChatPort,
        DocumentLoaderPort,
        EmbedPort,
        EvidencePort,
        KbDocumentRepoPort,
        ResearchPort,
        RetrievePort,
        RiskProfilePort,
        TaskRepoPort,
        UserRepoPort,
        WebSearchPort,
    )


class AppContainer:
    """12 个 Port + 5 个 use case 的中央配电盘。"""

    def __init__(
        self,
        settings: Settings,
        *,
        user_repo: UserRepoPort | None = None,
        task_repo: TaskRepoPort | None = None,
        audit_log: AuditLogPort | None = None,
        embedder: EmbedPort | None = None,
        chat: ChatPort | None = None,
        retriever: RetrievePort | None = None,
        web_search: WebSearchPort | None = None,
        evidence: EvidencePort | None = None,
        risk_profile: RiskProfilePort | None = None,
        research: ResearchPort | None = None,
        kb_repo: KbDocumentRepoPort | None = None,
        document_loader: DocumentLoaderPort | None = None,
        auth: AuthPort | None = None,
    ) -> None:
        self.settings = settings

        # ── 存储层：SQLite 单连接池给三个 repo 共享 ─────────────────────
        pool = (
            build_sqlite_pool(settings)
            if user_repo is None or task_repo is None or audit_log is None
            else None
        )
        self.user_repo: UserRepoPort = user_repo or build_user_repo(
            settings, pool=pool
        )
        self.task_repo: TaskRepoPort = task_repo or build_task_repo(
            settings, pool=pool
        )
        self.audit_log: AuditLogPort = audit_log or build_audit_log(
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
        self.research: ResearchPort = research or build_research(settings)
        self.kb_repo: KbDocumentRepoPort = kb_repo or build_kb_repo(settings)
        self.document_loader: DocumentLoaderPort = document_loader or build_document_loader(
            settings
        )

        # ── 鉴权（依赖 user_repo） ────────────────────────────────────
        self.auth: AuthPort = auth or build_auth(settings, self.user_repo)

        # ── use case 装配 ─────────────────────────────────────────────
        self.auth_login = AuthLoginUseCase(self.auth, audit_log=self.audit_log)
        self.task_management = TaskManagementUseCase(self.task_repo)
        self.ingest = IngestionUseCase(self.embedder)
        self.run_query = RunQueryUseCase(retriever=self.retriever, chat=self.chat)
        self.kb_management = KbManagementUseCase(
            kb_repo=self.kb_repo,
            loader=self.document_loader,
            embedder=self.embedder,
            audit_log=self.audit_log,
        )
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
            research=self.research,
        )

    # ─── 启动钩子（main.py lifespan 调用） ───────────────────────────

    def startup_migrations(self) -> int:
        """启动时一次性迁移：把缺 ``owner_id`` 的旧 chunk 标记为公共库。

        Step 025a 引入 ``owner_id`` 多租户隔离。已有 ChromaDB 数据无此字段，
        需要懒迁移：扫一次全集合，把 metadata 缺字段的标为 ``__public__``。
        幂等：迁移后再次调用为空操作。失败仅打 warning，不影响启动。

        Returns:
            本次迁移的 chunk 数；0 表示无需迁移或失败
        """
        import logging

        log = logging.getLogger(__name__)
        repo = self.kb_repo
        migrate = getattr(repo, "migrate_owner_id_legacy", None)
        if migrate is None:
            log.debug("kb_repo 未提供 migrate_owner_id_legacy，跳过启动迁移")
            return 0
        try:
            n = migrate()
            if n > 0:
                log.info("[Step 025a] owner_id 启动迁移完成：%d 个 chunk 标记为公共", n)
            else:
                log.debug("[Step 025a] owner_id 启动迁移：无需迁移")
            return n
        except Exception:
            log.warning("[Step 025a] owner_id 启动迁移失败（不影响启动）", exc_info=True)
            return 0


__all__ = ["AppContainer"]
