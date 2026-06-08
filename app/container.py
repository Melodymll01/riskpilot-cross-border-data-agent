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
    build_consolidation_state_store,
    build_consolidation_worker,
    build_document_loader,
    build_embedder,
    build_evidence,
    build_fact_store,
    build_kb_repo,
    build_memory,
    build_memory_scheduler,
    build_memory_settings_store,
    build_profile_store,
    build_research,
    build_retriever,
    build_risk_profile,
    build_sqlite_pool,
    build_task_repo,
    build_user_repo,
    build_web_search,
)
from app.memory import MemoryAssembler
from app.use_cases import (
    AuthLoginUseCase,
    IngestionUseCase,
    KbManagementUseCase,
    RunQueryUseCase,
    TaskManagementUseCase,
)
from app.use_cases.forget_memory import ForgetMemoryUseCase
from app.use_cases.memory_settings import MemorySettingsUseCase
from app.use_cases.run_copilot import RunCopilotUseCase

if TYPE_CHECKING:
    from config import Settings
    from domain.ports import (
        AuditLogPort,
        AuthPort,
        ChatPort,
        ConsolidationStatePort,
        DocumentLoaderPort,
        EmbedPort,
        EvidencePort,
        FactStorePort,
        KbDocumentRepoPort,
        MemoryJobSchedulerPort,
        MemoryPort,
        MemorySettingsStorePort,
        ProfileStorePort,
        ResearchPort,
        RetrievePort,
        RiskProfilePort,
        TaskRepoPort,
        UserRepoPort,
        WebSearchPort,
    )


class AppContainer:
    """13 个 Port + 5 个 use case 的中央配电盘。"""

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
        memory: MemoryPort | None = None,
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

        # ── 记忆系统（Step 030：S-030a L1 + S-030b L2 + S-030c L4 + S-030d L3/遗忘，依赖 task_repo） ───
        # memory 可为 None（禁用）；显式注入优先，否则按配置装配。
        # L4 fact_store / state_store 在 memory 与 worker 间共享，保证读写同一 collection。
        self.fact_store: FactStorePort | None = build_fact_store(settings)
        self.consolidation_state_store: ConsolidationStatePort | None = (
            build_consolidation_state_store(settings, task_repo=self.task_repo)
        )
        self.profile_store: ProfileStorePort | None = build_profile_store(
            settings, task_repo=self.task_repo
        )
        self.memory_settings_store: MemorySettingsStorePort | None = (
            build_memory_settings_store(settings, task_repo=self.task_repo)
        )
        self.memory: MemoryPort | None = memory or build_memory(
            settings,
            task_repo=self.task_repo,
            chat=self.chat,
            fact_store=self.fact_store,
            embedder=self.embedder,
            profile_store=self.profile_store,
            state_store=self.consolidation_state_store,
        )
        self.memory_assembler = MemoryAssembler(
            self.memory,
            recent_n=settings.memory_recent_n,
            token_budget=settings.memory_token_budget,
            recall_k=settings.memory_fact_recall_k,
            profile_max_facts=settings.memory_profile_max_facts,
            history_k=settings.memory_history_recall_k,
            settings_store=self.memory_settings_store,
        )
        # L4 提取-验证-巩固 worker（后台固化）
        self.consolidation_worker = build_consolidation_worker(
            settings,
            task_repo=self.task_repo,
            fact_store=self.fact_store,
            embedder=self.embedder,
            chat=self.chat,
            state_store=self.consolidation_state_store,
        )
        # L2/L4 后台调度器（回复后异步跳出摘要 / 固化）
        self.memory_scheduler: MemoryJobSchedulerPort | None = build_memory_scheduler(
            settings,
            memory=self.memory,
            consolidation_worker=self.consolidation_worker,
        )

        # ── use case 装配 ─────────────────────────────────────────────
        self.auth_login = AuthLoginUseCase(self.auth, audit_log=self.audit_log)
        self.task_management = TaskManagementUseCase(self.task_repo)
        self.forget_memory = ForgetMemoryUseCase(
            self.memory, audit_log=self.audit_log
        )
        self.memory_settings = MemorySettingsUseCase(
            self.memory_settings_store, audit_log=self.audit_log
        )
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
            memory_assembler=self.memory_assembler,
        )
        self.run_copilot = RunCopilotUseCase(
            agent=self.copilot_agent,
            task_management=self.task_management,
            risk_profile=self.risk_profile,
            research=self.research,
            memory_scheduler=self.memory_scheduler,
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
