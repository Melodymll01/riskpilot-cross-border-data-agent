"""Construct runtime-only model, retrieval, parsing, and verification adapters."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from langchain_core.language_models.chat_models import BaseChatModel

from domain.ports import (
    ChatPort,
    ClaimSupportVerifierPort,
    DocumentLoaderPort,
    DocumentParserPort,
    EmbedPort,
    EvidenceChunkerPort,
    EvidenceIndexPort,
    EvidenceQAGeneratorPort,
    KbDocumentRepoPort,
    MetricsPort,
    ParseArtifactStorePort,
    RetrievePort,
    TracePort,
    WebSearchPort,
)
from infra.chat import LangChainChatAdapter, build_chat_model
from infra.checkpoints import SqliteConversationCheckpointer
from infra.document_processing import RiskPilotDocumentParser
from infra.evidence import PageEvidenceChunker, SqliteEvidenceIndex
from infra.kb import ChromaKbRepo, UnifiedLoaderAdapter
from infra.observability import (
    CompositeTraceAdapter,
    LangSmithTraceAdapter,
    NoopMetricsAdapter,
    NoopTraceAdapter,
    OpenTelemetryTraceAdapter,
    PrometheusMetricsAdapter,
)
from infra.qa import StructuredClaimSupportVerifier, StructuredEvidenceQAGenerator
from infra.search import EmbedderAdapter, HybridRetrieverAdapter
from infra.storage import SqliteParseArtifactStore
from infra.storage._db import SqliteConnectionPool
from infra.web import DuckDuckGoAdapter

if TYPE_CHECKING:
    from config import Settings


def build_sqlite_pool(settings: Settings) -> SqliteConnectionPool:
    return SqliteConnectionPool(settings.artifact_db_path)


def build_conversation_checkpointer(settings: Settings) -> SqliteConversationCheckpointer:
    return SqliteConversationCheckpointer(settings.langgraph_checkpoint_db_path)


def build_document_parser(_settings: Settings) -> DocumentParserPort:
    import time

    return RiskPilotDocumentParser(clock=time.time)


def build_evidence_chunker(settings: Settings) -> EvidenceChunkerPort:
    import time

    return PageEvidenceChunker(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        clock=time.time,
    )


def build_embedder(settings: Settings) -> EmbedPort:
    from retrieval.search.embedder import Embedder

    return EmbedderAdapter(Embedder(config=settings))


def build_chat(settings: Settings) -> ChatPort:
    qwen_json_mode = (
        settings.llm_provider == "api"
        and settings.effective_chat_model.lower().startswith(("qwen3", "qwq"))
    )
    return LangChainChatAdapter(
        build_chat_model(settings),
        json_extra_body={"enable_thinking": False} if qwen_json_mode else None,
    )


def build_agent_model(settings: Settings) -> BaseChatModel:
    """Use the same cleaned model backend selection as the structured QA adapters."""
    return build_chat_model(settings)


def build_evidence_qa_generator(
    _settings: Settings,
    *,
    chat: ChatPort,
) -> EvidenceQAGeneratorPort:
    return StructuredEvidenceQAGenerator(chat)


def build_claim_support_verifier(
    _settings: Settings,
    *,
    chat: ChatPort,
) -> ClaimSupportVerifierPort:
    return StructuredClaimSupportVerifier(chat)


def build_retriever(settings: Settings) -> RetrievePort:
    return HybridRetrieverAdapter(model_settings=settings)


def build_web_search(_settings: Settings) -> WebSearchPort:
    return DuckDuckGoAdapter()


def build_trace(settings: Settings) -> TracePort:
    global_switches = {
        "LANGSMITH_TRACING": os.getenv("LANGSMITH_TRACING", ""),
        "LANGCHAIN_TRACING_V2": os.getenv("LANGCHAIN_TRACING_V2", ""),
    }
    enabled_switches = [
        name
        for name, value in global_switches.items()
        if value.strip().lower() in {"1", "true", "yes", "on"}
    ]
    if enabled_switches:
        raise ValueError(
            "禁止使用 LangSmith SDK 全局追踪开关 "
            f"{', '.join(enabled_switches)}；请改用 RISK_PILOT_LANGSMITH_ENABLED"
        )
    adapters: list[TracePort] = []
    if settings.otel_enabled:
        adapters.append(
            OpenTelemetryTraceAdapter(
                service_name=settings.otel_service_name,
                endpoint=settings.otel_exporter_otlp_endpoint,
                sampling_rate=settings.otel_sampling_rate,
                hash_salt=settings.observability_hash_salt,
            )
        )
    if settings.risk_pilot_langsmith_enabled:
        adapters.append(
            LangSmithTraceAdapter(
                api_key=settings.langsmith_api_key or "",
                endpoint=settings.langsmith_endpoint,
                project=settings.langsmith_project,
                sampling_rate=settings.langsmith_sampling_rate,
                hash_salt=settings.langsmith_hash_salt or "",
            )
        )
    if not adapters:
        return NoopTraceAdapter()
    if len(adapters) == 1:
        return adapters[0]
    return CompositeTraceAdapter(*adapters)


def build_metrics(settings: Settings) -> MetricsPort:
    if not settings.prometheus_enabled:
        return NoopMetricsAdapter()
    return PrometheusMetricsAdapter()


def build_document_loader(settings: Settings) -> DocumentLoaderPort:
    from processing.splitter import TextSplitter

    return UnifiedLoaderAdapter(splitter=TextSplitter(settings.chunk_size, settings.chunk_overlap))


def build_kb_repo(settings: Settings) -> KbDocumentRepoPort:
    from retrieval.search.vector_store import VectorStore

    return ChromaKbRepo(vector_store=VectorStore(config=settings))


def build_parse_artifact_store(
    settings: Settings, *, pool: SqliteConnectionPool | None = None
) -> ParseArtifactStorePort:
    return SqliteParseArtifactStore(pool or build_sqlite_pool(settings))


def build_evidence_index(
    settings: Settings, *, pool: SqliteConnectionPool | None = None
) -> EvidenceIndexPort:
    return SqliteEvidenceIndex(
        pool or build_sqlite_pool(settings), embedding_identity=settings.embedding_identity
    )
