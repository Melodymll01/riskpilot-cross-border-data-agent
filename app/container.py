"""DI for the conversation Agent, retrieval, files and persistence."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app import factories as f
from app.memory import MemoryAssembler
from app.use_cases.account_memory import AccountMemoryUseCase
from app.use_cases.auth_login import AuthLoginUseCase
from app.use_cases.document_management import DocumentManagementUseCase
from app.use_cases.evidence_qa import EvidenceQAUseCase
from app.use_cases.evidence_search import EvidenceSearchUseCase
from app.use_cases.feedback import FeedbackUseCase
from app.use_cases.kb_management import KbManagementUseCase
from app.use_cases.risk_assessment import RiskAssessmentUseCase
from app.use_cases.run_copilot import RunCopilotUseCase
from app.use_cases.task_management import TaskManagementUseCase
from app.workers import (
    DocumentOcrWorker,
    DocumentPipelineWorker,
    DocumentProcessingWorker,
    EvidenceIndexWorker,
)
from domain.memory_policy import MemoryPolicy
from infra.agents import LangChainComplianceAgent
from infra.auth.gateway_identity import GatewayOnlyAuth, ReadOnlyGatewayUsers
from infra.checkpoints import SqliteConversationCheckpointer
from infra.document_processing import RapidOcrDocumentAdapter
from infra.storage.sqlite_account_memory_repo import SqliteAccountMemoryRepo
from infra.storage.sqlite_risk_repo import SqliteRiskAssessmentRepo
from infra.tasks.thread_dispatcher import ThreadDocumentDispatcher

if TYPE_CHECKING:
    from config import Settings
    from domain.ports import (
        AuditLogPort,
        AuthPort,
        BackgroundJobDispatcherPort,
        ChatPort,
        ClaimSupportVerifierPort,
        ConversationRunRepoPort,
        DocumentLoaderPort,
        DocumentOcrPort,
        DocumentParserPort,
        DocumentRepoPort,
        EmbedPort,
        EvidenceChunkerPort,
        EvidenceIndexPort,
        EvidenceQAGeneratorPort,
        KbDocumentRepoPort,
        MemoryPort,
        MetricsPort,
        ObjectStorePort,
        ReadinessPort,
        RetrievePort,
        TaskRepoPort,
        TracePort,
        UserRepoPort,
        WebSearchPort,
    )


class AppContainer:
    def __init__(
        self,
        settings: Settings,
        *,
        user_repo: UserRepoPort | None = None,
        task_repo: TaskRepoPort | None = None,
        conversation_run_repo: ConversationRunRepoPort | None = None,
        audit_log: AuditLogPort | None = None,
        embedder: EmbedPort | None = None,
        chat: ChatPort | None = None,
        retriever: RetrievePort | None = None,
        web_search: WebSearchPort | None = None,
        kb_repo: KbDocumentRepoPort | None = None,
        document_loader: DocumentLoaderPort | None = None,
        document_repo: DocumentRepoPort | None = None,
        document_parser: DocumentParserPort | None = None,
        document_ocr: DocumentOcrPort | None = None,
        evidence_chunker: EvidenceChunkerPort | None = None,
        evidence_index: EvidenceIndexPort | None = None,
        object_store: ObjectStorePort | None = None,
        job_dispatcher: BackgroundJobDispatcherPort | None = None,
        evidence_qa_generator: EvidenceQAGeneratorPort | None = None,
        claim_support_verifier: ClaimSupportVerifierPort | None = None,
        auth: AuthPort | None = None,
        memory: MemoryPort | None = None,
        trace: TracePort | None = None,
        metrics: MetricsPort | None = None,
        readiness: ReadinessPort | None = None,
        agent_model: Any | None = None,
        checkpoint_runtime: SqliteConversationCheckpointer | None = None,
        agent_interrupt_after: list[str] | None = None,
    ) -> None:
        self.settings = settings
        self.pool = f.build_sqlite_pool(settings)
        self.trace = trace or f.build_trace(settings)
        self.metrics = metrics or f.build_metrics(settings)
        self.user_repo = user_repo or f.build_user_repo(settings, pool=self.pool)
        if settings.internal_service_required:
            self.user_repo = ReadOnlyGatewayUsers(self.user_repo)
        self.task_repo = task_repo or f.build_task_repo(settings, pool=self.pool)
        self.account_memory_policy = MemoryPolicy(
            business_ttl_days=settings.memory_business_ttl_days,
            profile_ttl_days=settings.memory_profile_ttl_days,
            profile_review_days=settings.memory_profile_review_days,
            context_chars=min(settings.memory_account_context_chars, settings.memory_token_budget),
            context_recent_n=settings.memory_recent_n if settings.memory_enabled else 0,
            context_ttl_days=settings.memory_l1_ttl_days,
        )
        self.account_memory_repo = SqliteAccountMemoryRepo(
            self.pool, policy=self.account_memory_policy
        )
        self.account_memory = AccountMemoryUseCase(
            self.account_memory_repo,
            self.user_repo,
            self.task_repo,
            policy=self.account_memory_policy,
        )
        self.conversation_run_repo = conversation_run_repo or f.build_conversation_run_repo(
            settings, pool=self.pool
        )
        self.document_repo = document_repo or f.build_document_repo(settings, pool=self.pool)
        self.audit_log = audit_log or f.build_audit_log(settings, pool=self.pool)
        self.evidence_index = evidence_index or f.build_evidence_index(settings, pool=self.pool)
        self.readiness = readiness or f.build_readiness(settings, database=self.pool)
        self.embedder = embedder or f.build_embedder(settings)
        self.chat = chat or f.build_chat(settings)
        self.retriever = retriever or f.build_retriever(settings)
        self.web_search = web_search or f.build_web_search(settings)
        self.kb_repo = kb_repo or f.build_kb_repo(settings)
        self.document_loader = document_loader or f.build_document_loader(settings)
        self.object_store = object_store or f.build_object_store(settings)
        self.document_parser = document_parser or f.build_document_parser(settings)
        self.document_ocr = document_ocr or RapidOcrDocumentAdapter()
        self.evidence_chunker = evidence_chunker or f.build_evidence_chunker(settings)
        self.evidence_qa_generator = evidence_qa_generator or f.build_evidence_qa_generator(
            settings, chat=self.chat
        )
        self.claim_support_verifier = claim_support_verifier or f.build_claim_support_verifier(
            settings, chat=self.chat
        )
        self.auth = (
            GatewayOnlyAuth()
            if settings.internal_service_required
            else (auth or f.build_auth(settings, self.user_repo))
        )
        self.memory = memory or f.build_memory(settings, task_repo=self.task_repo, chat=self.chat)

        self.memory_assembler = MemoryAssembler(
            self.memory,
            recent_n=settings.memory_recent_n,
            token_budget=settings.memory_token_budget,
        )
        self.memory_scheduler = f.build_memory_scheduler(settings, memory=self.memory)
        self.checkpoint_runtime = checkpoint_runtime or f.build_conversation_checkpointer(settings)
        self.feedback_repo = f.build_feedback_repo(settings, pool=self.pool)
        self.auth_login = AuthLoginUseCase(self.auth, audit_log=self.audit_log)
        self.task_management = TaskManagementUseCase(self.task_repo)
        self.document_processing_worker = DocumentProcessingWorker(
            document_repo=self.document_repo,
            object_store=self.object_store,
            parser=self.document_parser,
        )
        self.document_ocr_worker = DocumentOcrWorker(
            document_repo=self.document_repo, object_store=self.object_store, ocr=self.document_ocr
        )
        self.evidence_index_worker = EvidenceIndexWorker(
            document_repo=self.document_repo,
            chunker=self.evidence_chunker,
            evidence_index=self.evidence_index,
            embedder=self.embedder,
        )
        self.document_pipeline = DocumentPipelineWorker(
            document_repo=self.document_repo,
            parser=self.document_processing_worker,
            ocr=self.document_ocr_worker,
            indexer=self.evidence_index_worker,
        )
        self.job_dispatcher = job_dispatcher or ThreadDocumentDispatcher(self.document_pipeline)
        self.document_management = DocumentManagementUseCase(
            document_repo=self.document_repo,
            object_store=self.object_store,
            max_upload_bytes=settings.max_upload_mb * 1024 * 1024,
            job_dispatcher=self.job_dispatcher,
            processing_worker=self.document_processing_worker,
            index_worker=self.evidence_index_worker,
        )
        self.evidence_search = EvidenceSearchUseCase(
            evidence_index=self.evidence_index, embedder=self.embedder
        )
        self.evidence_qa = EvidenceQAUseCase(
            retriever=self.retriever,
            evidence_index=self.evidence_index,
            document_repo=self.document_repo,
            embedder=self.embedder,
            generator=self.evidence_qa_generator,
            support_verifier=self.claim_support_verifier,
            trace=self.trace,
            metrics=self.metrics,
        )
        self.feedback = FeedbackUseCase(self.feedback_repo)
        self.risk_repo = SqliteRiskAssessmentRepo(self.pool)
        self.risk_assessment = RiskAssessmentUseCase(
            self.chat, self.evidence_qa, self.risk_repo, trace=self.trace
        )
        self.kb_management = KbManagementUseCase(
            kb_repo=self.kb_repo,
            loader=self.document_loader,
            embedder=self.embedder,
            audit_log=self.audit_log,
        )
        self.agent_model = agent_model or f.build_agent_model(settings)
        self.copilot_agent = LangChainComplianceAgent(
            model=self.agent_model,
            task_repo=self.task_repo,
            run_repo=self.conversation_run_repo,
            retriever=self.retriever,
            web_search=self.web_search,
            evidence_qa=self.evidence_qa,
            checkpointer=self.checkpoint_runtime.saver,
            memory_assembler=self.memory_assembler,
            trace=self.trace,
            metrics=self.metrics,
            model_name=settings.effective_chat_model,
            evidence_search=self.evidence_search,
            interrupt_after=agent_interrupt_after,
            account_memory=self.account_memory,
            risk_assessment=self.risk_assessment,
        )
        self.run_copilot = RunCopilotUseCase(
            agent=self.copilot_agent,
            task_management=self.task_management,
            run_repo=self.conversation_run_repo,
            memory_scheduler=self.memory_scheduler,
            documents=self.document_management,
            run_timeout_seconds=settings.conversation_run_timeout_seconds,
            model_call_timeout_seconds=settings.conversation_model_call_timeout_seconds,
            tool_call_timeout_seconds=settings.conversation_tool_call_timeout_seconds,
            max_model_calls=settings.conversation_max_model_calls,
            max_tool_calls=settings.conversation_max_tool_calls,
            control_poll_seconds=settings.conversation_control_poll_seconds,
            trace=self.trace,
            account_memory=self.account_memory,
        )

    def close(self) -> None:
        for resource in (
            self.job_dispatcher,
            self.memory_scheduler,
            self.checkpoint_runtime,
            self.trace,
        ):
            shutdown = getattr(resource, "shutdown", None)
            if callable(shutdown):
                shutdown()
        self.pool.close()
