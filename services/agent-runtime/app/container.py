"""Dependency injection for the private Agent runtime."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app import factories as f
from app.document_processing import InternalDocumentService
from app.internal_runtime import InternalRuntimeService
from app.use_cases.evidence_qa import EvidenceQAUseCase
from app.use_cases.evidence_search import EvidenceSearchUseCase
from infra.agents import LangChainComplianceAgent

if TYPE_CHECKING:
    from config import Settings
    from domain.ports import (
        ChatPort,
        ClaimSupportVerifierPort,
        EmbedPort,
        EvidenceIndexPort,
        EvidenceQAGeneratorPort,
        KbDocumentRepoPort,
        MetricsPort,
        ParseArtifactStorePort,
        RetrievePort,
        TracePort,
        WebSearchPort,
    )
    from infra.checkpoints import SqliteConversationCheckpointer


class AppContainer:
    def __init__(
        self,
        settings: Settings,
        *,
        embedder: EmbedPort | None = None,
        chat: ChatPort | None = None,
        retriever: RetrievePort | None = None,
        web_search: WebSearchPort | None = None,
        parse_artifacts: ParseArtifactStorePort | None = None,
        evidence_index: EvidenceIndexPort | None = None,
        evidence_qa_generator: EvidenceQAGeneratorPort | None = None,
        claim_support_verifier: ClaimSupportVerifierPort | None = None,
        trace: TracePort | None = None,
        metrics: MetricsPort | None = None,
        agent_model: Any | None = None,
        kb_repo: KbDocumentRepoPort | None = None,
        checkpoint_runtime: SqliteConversationCheckpointer | None = None,
        agent_interrupt_after: list[str] | None = None,
        agent: LangChainComplianceAgent | None = None,
    ) -> None:
        self.settings = settings
        self.pool = f.build_sqlite_pool(settings)
        self.trace = trace or f.build_trace(settings)
        self.metrics = metrics or f.build_metrics(settings)
        self.parse_artifacts = parse_artifacts or f.build_parse_artifact_store(
            settings, pool=self.pool
        )
        self.evidence_index = evidence_index or f.build_evidence_index(settings, pool=self.pool)
        self.embedder = embedder or f.build_embedder(settings)
        self.chat = chat or f.build_chat(settings)
        self.retriever = retriever or f.build_retriever(settings)
        self.web_search = web_search or f.build_web_search(settings)
        self.kb_repo = kb_repo or f.build_kb_repo(settings)
        self.document_loader = f.build_document_loader(settings)
        self.document_parser = f.build_document_parser(settings)
        self.evidence_chunker = f.build_evidence_chunker(settings)
        self.evidence_qa_generator = evidence_qa_generator or f.build_evidence_qa_generator(
            settings, chat=self.chat
        )
        self.claim_support_verifier = claim_support_verifier or f.build_claim_support_verifier(
            settings, chat=self.chat
        )
        self.evidence_search = EvidenceSearchUseCase(
            evidence_index=self.evidence_index,
            embedder=self.embedder,
        )
        self.evidence_qa = EvidenceQAUseCase(
            retriever=self.retriever,
            evidence_index=self.evidence_index,
            parse_artifacts=self.parse_artifacts,
            embedder=self.embedder,
            generator=self.evidence_qa_generator,
            support_verifier=self.claim_support_verifier,
            trace=self.trace,
            metrics=self.metrics,
        )
        self.checkpoint_runtime = checkpoint_runtime or f.build_conversation_checkpointer(settings)
        self.agent_model = agent_model or f.build_agent_model(settings)
        self.agent = agent or LangChainComplianceAgent(
            model=self.agent_model,
            retriever=self.retriever,
            web_search=self.web_search,
            evidence_qa=self.evidence_qa,
            checkpointer=self.checkpoint_runtime.saver,
            trace=self.trace,
            metrics=self.metrics,
            model_name=settings.effective_chat_model,
            evidence_search=self.evidence_search,
            interrupt_after=agent_interrupt_after,
        )
        self.runtime = InternalRuntimeService(
            self.agent,
            model_call_timeout_seconds=settings.conversation_model_call_timeout_seconds,
            tool_call_timeout_seconds=settings.conversation_tool_call_timeout_seconds,
            poll_seconds=settings.conversation_control_poll_seconds,
        )
        self.documents = InternalDocumentService(
            loader=self.document_loader,
            parser=self.document_parser,
            chunker=self.evidence_chunker,
            embedder=self.embedder,
            kb_repo=self.kb_repo,
            parse_artifacts=self.parse_artifacts,
            evidence_index=self.evidence_index,
        )

    def close(self) -> None:
        self.runtime.close()
        for resource in (self.checkpoint_runtime, self.trace):
            shutdown = getattr(resource, "shutdown", None)
            if callable(shutdown):
                shutdown()
        self.pool.close()
