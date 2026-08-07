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
    build_assessment_repo,
    build_audit_log,
    build_auth,
    build_case_fact_repo,
    build_case_repo,
    build_chat,
    build_consolidation_state_store,
    build_consolidation_worker,
    build_document_loader,
    build_document_parser,
    build_document_repo,
    build_embedder,
    build_evidence,
    build_evidence_chunker,
    build_evidence_index,
    build_fact_store,
    build_feedback_repo,
    build_kb_repo,
    build_memory,
    build_memory_scheduler,
    build_memory_settings_store,
    build_object_store,
    build_policy_rule_repo,
    build_profile_store,
    build_research,
    build_retriever,
    build_risk_profile,
    build_sqlite_pool,
    build_task_repo,
    build_user_repo,
    build_web_search,
    build_workspace_repo,
)
from app.memory import MemoryAssembler
from app.use_cases import (
    AssessmentManagementUseCase,
    AuthLoginUseCase,
    CaseManagementUseCase,
    DocumentManagementUseCase,
    EvidenceSearchUseCase,
    FactManagementUseCase,
    IngestionUseCase,
    KbManagementUseCase,
    PolicyManagementUseCase,
    RunQueryUseCase,
    TaskManagementUseCase,
    WorkspaceManagementUseCase,
)
from app.use_cases.feedback import FeedbackUseCase
from app.use_cases.forget_memory import ForgetMemoryUseCase
from app.use_cases.memory_settings import MemorySettingsUseCase
from app.use_cases.run_copilot import RunCopilotUseCase

if TYPE_CHECKING:
    from config import Settings
    from domain.ports import (
        AssessmentRepoPort,
        AuditLogPort,
        AuthPort,
        CaseFactRepoPort,
        CaseRepoPort,
        ChatPort,
        ConsolidationStatePort,
        DocumentLoaderPort,
        DocumentParserPort,
        DocumentRepoPort,
        EmbedPort,
        EvidenceChunkerPort,
        EvidenceIndexPort,
        EvidencePort,
        FactStorePort,
        KbDocumentRepoPort,
        MemoryJobSchedulerPort,
        MemoryPort,
        MemorySettingsStorePort,
        ObjectStorePort,
        PolicyRuleRepoPort,
        ProfileStorePort,
        ResearchPort,
        RetrievePort,
        RiskProfilePort,
        TaskRepoPort,
        UserRepoPort,
        WebSearchPort,
        WorkspaceRepoPort,
    )


class AppContainer:
    """13 个 Port + 5 个 use case 的中央配电盘。"""

    def __init__(
        self,
        settings: Settings,
        *,
        assessment_repo: AssessmentRepoPort | None = None,
        user_repo: UserRepoPort | None = None,
        task_repo: TaskRepoPort | None = None,
        workspace_repo: WorkspaceRepoPort | None = None,
        case_repo: CaseRepoPort | None = None,
        case_fact_repo: CaseFactRepoPort | None = None,
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
        document_parser: DocumentParserPort | None = None,
        document_repo: DocumentRepoPort | None = None,
        evidence_chunker: EvidenceChunkerPort | None = None,
        evidence_index: EvidenceIndexPort | None = None,
        policy_rule_repo: PolicyRuleRepoPort | None = None,
        object_store: ObjectStorePort | None = None,
        auth: AuthPort | None = None,
        memory: MemoryPort | None = None,
    ) -> None:
        self.settings = settings

        # ── 存储层：SQLite 单连接池给三个 repo 共享 ─────────────────────
        pool = (
            build_sqlite_pool(settings)
            if (
                assessment_repo is None
                or user_repo is None
                or task_repo is None
                or workspace_repo is None
                or case_repo is None
                or case_fact_repo is None
                or document_repo is None
                or evidence_index is None
                or policy_rule_repo is None
                or audit_log is None
            )
            else None
        )
        self.assessment_repo: AssessmentRepoPort = (
            assessment_repo or build_assessment_repo(settings, pool=pool)
        )
        self.user_repo: UserRepoPort = user_repo or build_user_repo(
            settings, pool=pool
        )
        self.task_repo: TaskRepoPort = task_repo or build_task_repo(
            settings, pool=pool
        )
        self.workspace_repo: WorkspaceRepoPort = (
            workspace_repo or build_workspace_repo(settings, pool=pool)
        )
        self.case_repo: CaseRepoPort = case_repo or build_case_repo(
            settings, pool=pool
        )
        self.case_fact_repo: CaseFactRepoPort = (
            case_fact_repo or build_case_fact_repo(settings, pool=pool)
        )
        self.document_repo: DocumentRepoPort = document_repo or build_document_repo(
            settings, pool=pool
        )
        self.evidence_index: EvidenceIndexPort = (
            evidence_index or build_evidence_index(settings, pool=pool)
        )
        self.policy_rule_repo: PolicyRuleRepoPort = (
            policy_rule_repo or build_policy_rule_repo(settings, pool=pool)
        )
        self.audit_log: AuditLogPort = audit_log or build_audit_log(
            settings, pool=pool
        )
        # 消息反馈（点赞/点踩统计）复用同一连接池。
        self.feedback_repo = build_feedback_repo(settings, pool=pool)

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
        self.object_store: ObjectStorePort = object_store or build_object_store(settings)
        self.document_parser: DocumentParserPort = (
            document_parser or build_document_parser(settings)
        )
        self.evidence_chunker: EvidenceChunkerPort = (
            evidence_chunker or build_evidence_chunker(settings)
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
        self.workspace_management = WorkspaceManagementUseCase(self.workspace_repo)
        self.case_management = CaseManagementUseCase(
            case_repo=self.case_repo,
            workspace_repo=self.workspace_repo,
        )
        self.document_management = DocumentManagementUseCase(
            document_repo=self.document_repo,
            object_store=self.object_store,
            case_management=self.case_management,
            workspace_management=self.workspace_management,
            max_upload_bytes=settings.max_upload_mb * 1024 * 1024,
        )
        from app.workers import DocumentProcessingWorker, EvidenceIndexWorker

        self.document_processing_worker = DocumentProcessingWorker(
            document_repo=self.document_repo,
            object_store=self.object_store,
            parser=self.document_parser,
        )
        self.document_management.bind_processing_worker(
            self.document_processing_worker
        )
        self.evidence_index_worker = EvidenceIndexWorker(
            document_repo=self.document_repo,
            chunker=self.evidence_chunker,
            evidence_index=self.evidence_index,
            embedder=self.embedder,
        )
        self.document_management.bind_index_worker(self.evidence_index_worker)
        self.evidence_search = EvidenceSearchUseCase(
            evidence_index=self.evidence_index,
            embedder=self.embedder,
            case_management=self.case_management,
        )
        self.fact_management = FactManagementUseCase(
            fact_repo=self.case_fact_repo,
            document_repo=self.document_repo,
            case_management=self.case_management,
            workspace_management=self.workspace_management,
        )
        self.policy_management = PolicyManagementUseCase(
            rule_repo=self.policy_rule_repo,
            fact_repo=self.case_fact_repo,
            case_management=self.case_management,
            workspace_management=self.workspace_management,
        )
        self.assessment_management = AssessmentManagementUseCase(
            assessment_repo=self.assessment_repo,
            fact_repo=self.case_fact_repo,
            case_management=self.case_management,
            workspace_management=self.workspace_management,
            policy_management=self.policy_management,
        )
        self.feedback = FeedbackUseCase(self.feedback_repo)
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
