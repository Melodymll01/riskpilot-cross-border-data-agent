"""``AppContainer``：DI 装配中心。

职责：
- 一次性装配所有 Port，Repository 共享 SQLite 连接池
- 支持"全工厂"模式（生产）与"全注入"模式（测试）混用
- 统一装配应用 Use Case、LangChain Agent 与 LangGraph Runtime

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

from typing import TYPE_CHECKING, Any

from app.agent_tools import build_case_assessment_tool_registry
from app.factories import (
    build_agent_model,
    build_agent_run_repo,
    build_assessment_repo,
    build_audit_log,
    build_auth,
    build_case_fact_repo,
    build_case_repo,
    build_chat,
    build_claim_support_verifier,
    build_consolidation_state_store,
    build_consolidation_worker,
    build_document_loader,
    build_document_parser,
    build_document_repo,
    build_embedder,
    build_evidence_chunker,
    build_evidence_index,
    build_evidence_planner,
    build_evidence_qa_generator,
    build_fact_proposal_generator,
    build_fact_store,
    build_feedback_repo,
    build_job_dispatcher,
    build_kb_repo,
    build_memory,
    build_memory_scheduler,
    build_memory_settings_store,
    build_object_store,
    build_policy_rule_repo,
    build_profile_store,
    build_readiness,
    build_research,
    build_retriever,
    build_risk_profile,
    build_sqlalchemy_database,
    build_sqlite_pool,
    build_task_repo,
    build_trace,
    build_user_repo,
    build_visual_embedder,
    build_visual_index,
    build_web_search,
    build_workflow_runtime,
    build_workspace_repo,
)
from app.memory import MemoryAssembler
from app.use_cases import (
    AssessmentManagementUseCase,
    AssessmentRunUseCase,
    AuthLoginUseCase,
    CaseManagementUseCase,
    DocumentManagementUseCase,
    EvidenceQAUseCase,
    EvidenceSearchUseCase,
    FactManagementUseCase,
    KbManagementUseCase,
    PolicyManagementUseCase,
    TaskManagementUseCase,
    VisualEvidenceUseCase,
    WorkspaceManagementUseCase,
)
from app.use_cases.feedback import FeedbackUseCase
from app.use_cases.forget_memory import ForgetMemoryUseCase
from app.use_cases.memory_settings import MemorySettingsUseCase
from app.use_cases.run_copilot import RunCopilotUseCase
from infra.agents import LangChainComplianceAgent

if TYPE_CHECKING:
    from config import Settings
    from domain.ports import (
        AgentRunRepoPort,
        AssessmentRepoPort,
        AuditLogPort,
        AuthPort,
        BackgroundJobDispatcherPort,
        CaseAssessmentToolPort,
        CaseFactRepoPort,
        CaseRepoPort,
        ChatPort,
        ClaimSupportVerifierPort,
        ConsolidationStatePort,
        DocumentLoaderPort,
        DocumentParserPort,
        DocumentRepoPort,
        EmbedPort,
        EvidenceChunkerPort,
        EvidenceIndexPort,
        EvidencePlannerPort,
        EvidenceQAGeneratorPort,
        FactProposalGeneratorPort,
        FactStorePort,
        KbDocumentRepoPort,
        MemoryJobSchedulerPort,
        MemoryPort,
        MemorySettingsStorePort,
        ObjectStorePort,
        PolicyRuleRepoPort,
        ProfileStorePort,
        ReadinessPort,
        ResearchPort,
        RetrievePort,
        RiskProfilePort,
        TaskRepoPort,
        TracePort,
        UserRepoPort,
        VisualEmbedPort,
        VisualIndexPort,
        WebSearchPort,
        WorkflowRuntimePort,
        WorkspaceRepoPort,
    )


class AppContainer:
    """应用 Port、Use Case 与 AI Runtime 的中央配电盘。"""

    def __init__(
        self,
        settings: Settings,
        *,
        agent_run_repo: AgentRunRepoPort | None = None,
        assessment_repo: AssessmentRepoPort | None = None,
        user_repo: UserRepoPort | None = None,
        task_repo: TaskRepoPort | None = None,
        workspace_repo: WorkspaceRepoPort | None = None,
        case_repo: CaseRepoPort | None = None,
        case_fact_repo: CaseFactRepoPort | None = None,
        audit_log: AuditLogPort | None = None,
        embedder: EmbedPort | None = None,
        chat: ChatPort | None = None,
        evidence_qa_generator: EvidenceQAGeneratorPort | None = None,
        claim_support_verifier: ClaimSupportVerifierPort | None = None,
        fact_proposal_generator: FactProposalGeneratorPort | None = None,
        retriever: RetrievePort | None = None,
        web_search: WebSearchPort | None = None,
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
        job_dispatcher: BackgroundJobDispatcherPort | None = None,
        workflow_runtime: WorkflowRuntimePort | None = None,
        evidence_planner: EvidencePlannerPort | None = None,
        case_assessment_tools: CaseAssessmentToolPort | None = None,
        visual_index: VisualIndexPort | None = None,
        visual_embedder: VisualEmbedPort | None = None,
        auth: AuthPort | None = None,
        memory: MemoryPort | None = None,
        trace: TracePort | None = None,
        readiness: ReadinessPort | None = None,
        agent_model: Any | None = None,
    ) -> None:
        self.settings = settings
        self.trace: TracePort = trace or build_trace(settings)

        # ── 存储层：SQLite 单连接池给三个 repo 共享 ─────────────────────
        pool = (
            build_sqlite_pool(settings)
            if (
                agent_run_repo is None
                or assessment_repo is None
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
        sqlalchemy_database = (
            build_sqlalchemy_database(settings)
            if settings.storage_backend == "postgres"
            and (
                agent_run_repo is None
                or assessment_repo is None
                or workspace_repo is None
                or case_repo is None
                or case_fact_repo is None
                or document_repo is None
                or evidence_index is None
                or policy_rule_repo is None
            )
            else None
        )
        self.storage_database = sqlalchemy_database
        self.agent_run_repo: AgentRunRepoPort = agent_run_repo or build_agent_run_repo(
            settings, pool=pool, database=sqlalchemy_database
        )
        self.assessment_repo: AssessmentRepoPort = assessment_repo or build_assessment_repo(
            settings, pool=pool, database=sqlalchemy_database
        )
        self.user_repo: UserRepoPort = user_repo or build_user_repo(settings, pool=pool)
        self.task_repo: TaskRepoPort = task_repo or build_task_repo(settings, pool=pool)
        self.workspace_repo: WorkspaceRepoPort = workspace_repo or build_workspace_repo(
            settings, pool=pool, database=sqlalchemy_database
        )
        self.case_repo: CaseRepoPort = case_repo or build_case_repo(
            settings, pool=pool, database=sqlalchemy_database
        )
        self.case_fact_repo: CaseFactRepoPort = case_fact_repo or build_case_fact_repo(
            settings, pool=pool, database=sqlalchemy_database
        )
        self.document_repo: DocumentRepoPort = document_repo or build_document_repo(
            settings, pool=pool, database=sqlalchemy_database
        )
        self.evidence_index: EvidenceIndexPort = evidence_index or build_evidence_index(
            settings, pool=pool, database=sqlalchemy_database
        )
        self.policy_rule_repo: PolicyRuleRepoPort = policy_rule_repo or build_policy_rule_repo(
            settings, pool=pool, database=sqlalchemy_database
        )
        self.audit_log: AuditLogPort = audit_log or build_audit_log(settings, pool=pool)
        self.readiness: ReadinessPort = readiness or build_readiness(
            settings,
            database=sqlalchemy_database or pool,
        )
        # 消息反馈（点赞/点踩统计）复用同一连接池。
        self.feedback_repo = build_feedback_repo(settings, pool=pool)

        # ── LLM / 检索 / 外部 ─────────────────────────────────────────
        self.embedder: EmbedPort = embedder or build_embedder(settings)
        self.chat: ChatPort = chat or build_chat(settings)
        self.evidence_qa_generator: EvidenceQAGeneratorPort = (
            evidence_qa_generator or build_evidence_qa_generator(settings, chat=self.chat)
        )
        self.claim_support_verifier: ClaimSupportVerifierPort = (
            claim_support_verifier or build_claim_support_verifier(settings, chat=self.chat)
        )
        self.fact_proposal_generator: FactProposalGeneratorPort = (
            fact_proposal_generator or build_fact_proposal_generator(settings, chat=self.chat)
        )
        self.retriever: RetrievePort = retriever or build_retriever(settings)
        self.web_search: WebSearchPort = web_search or build_web_search(settings)
        self.risk_profile: RiskProfilePort = risk_profile or build_risk_profile(
            settings,
            trace=self.trace,
        )
        self.kb_repo: KbDocumentRepoPort = kb_repo or build_kb_repo(settings)
        self.document_loader: DocumentLoaderPort = document_loader or build_document_loader(
            settings
        )
        self.object_store: ObjectStorePort = object_store or build_object_store(settings)
        self.job_dispatcher: BackgroundJobDispatcherPort = job_dispatcher or build_job_dispatcher(
            settings
        )
        self.visual_index = visual_index or build_visual_index(settings, pool=pool)
        self.visual_embedder = visual_embedder or build_visual_embedder(settings)
        self.document_parser: DocumentParserPort = document_parser or build_document_parser(
            settings
        )
        self.evidence_chunker: EvidenceChunkerPort = evidence_chunker or build_evidence_chunker(
            settings
        )
        self.research: ResearchPort = research or build_research(
            settings,
            retriever=self.retriever,
            web_search=self.web_search,
            chat=self.chat,
            trace=self.trace,
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
        self.memory_settings_store: MemorySettingsStorePort | None = build_memory_settings_store(
            settings, task_repo=self.task_repo
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
            job_dispatcher=self.job_dispatcher,
        )
        from app.workers import DocumentProcessingWorker, EvidenceIndexWorker

        self.document_processing_worker = DocumentProcessingWorker(
            document_repo=self.document_repo,
            object_store=self.object_store,
            parser=self.document_parser,
        )
        self.document_management.bind_processing_worker(self.document_processing_worker)
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
        self.visual_evidence = VisualEvidenceUseCase(
            visual_index=self.visual_index,
            embedder=self.visual_embedder,
            object_store=self.object_store,
            case_management=self.case_management,
            workspace_management=self.workspace_management,
            max_upload_bytes=settings.visual_max_upload_mb * 1024 * 1024,
        )
        self.fact_management = FactManagementUseCase(
            fact_repo=self.case_fact_repo,
            document_repo=self.document_repo,
            proposal_generator=self.fact_proposal_generator,
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
            document_repo=self.document_repo,
            case_management=self.case_management,
            workspace_management=self.workspace_management,
            policy_management=self.policy_management,
        )
        self.agent_model = agent_model or build_agent_model(settings)
        self.case_assessment_tools: CaseAssessmentToolPort = (
            case_assessment_tools
            or build_case_assessment_tool_registry(
                evidence_search=self.evidence_search,
                policy_management=self.policy_management,
                fact_management=self.fact_management,
                assessment_management=self.assessment_management,
            )
        )
        self.evidence_planner: EvidencePlannerPort = evidence_planner or build_evidence_planner(
            settings, model=self.agent_model
        )
        self.workflow_runtime: WorkflowRuntimePort = workflow_runtime or build_workflow_runtime(
            settings,
            trace=self.trace,
            planner=self.evidence_planner,
            tools=self.case_assessment_tools,
        )
        self.evidence_qa = EvidenceQAUseCase(
            retriever=self.retriever,
            evidence_index=self.evidence_index,
            document_repo=self.document_repo,
            embedder=self.embedder,
            generator=self.evidence_qa_generator,
            support_verifier=self.claim_support_verifier,
            workspace_management=self.workspace_management,
            case_management=self.case_management,
            assessment_management=self.assessment_management,
        )
        self.assessment_runs = AssessmentRunUseCase(
            run_repo=self.agent_run_repo,
            workflow_runtime=self.workflow_runtime,
            document_repo=self.document_repo,
            fact_repo=self.case_fact_repo,
            case_management=self.case_management,
            workspace_management=self.workspace_management,
            policy_management=self.policy_management,
            assessment_management=self.assessment_management,
        )
        self.feedback = FeedbackUseCase(self.feedback_repo)
        self.forget_memory = ForgetMemoryUseCase(self.memory, audit_log=self.audit_log)
        self.memory_settings = MemorySettingsUseCase(
            self.memory_settings_store, audit_log=self.audit_log
        )
        self.kb_management = KbManagementUseCase(
            kb_repo=self.kb_repo,
            loader=self.document_loader,
            embedder=self.embedder,
            audit_log=self.audit_log,
        )
        self.copilot_agent = LangChainComplianceAgent(
            model=self.agent_model,
            task_repo=self.task_repo,
            retriever=self.retriever,
            web_search=self.web_search,
            risk_profile=self.risk_profile,
            memory_assembler=self.memory_assembler,
            trace=self.trace,
        )
        self.run_copilot = RunCopilotUseCase(
            agent=self.copilot_agent,
            task_management=self.task_management,
            risk_profile=self.risk_profile,
            research=self.research,
            memory_scheduler=self.memory_scheduler,
        )


__all__ = ["AppContainer"]
