"""Ports retained by the private Agent runtime and its RAG pipeline."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from typing import Any, Literal, Protocol, TypeVar, runtime_checkable

from domain.document_content import DocumentParseSnapshot
from domain.documents import DocumentVersion
from domain.evidence import EvidenceChunk, EvidenceSearchHit
from domain.models import ChatResponse, Chunk, KbChunk, KbDocument, WebResult
from domain.qa import (
    ClaimSupportResult,
    EvidenceQACitation,
    EvidenceQAClaim,
    EvidenceQADraft,
    UserContextMessage,
)

T = TypeVar("T")


@runtime_checkable
class ParseArtifactStorePort(Protocol):
    """Python-owned parse output keyed by the immutable version supplied by Go."""

    def save(
        self,
        *,
        owner_id: str,
        document_id: str,
        document_version_id: str,
        source_sha256: str,
        snapshot: DocumentParseSnapshot,
    ) -> None: ...

    def get(
        self,
        *,
        owner_id: str,
        document_id: str,
        document_version_id: str,
        source_sha256: str,
    ) -> DocumentParseSnapshot | None: ...

    def delete(
        self,
        *,
        owner_id: str,
        document_id: str,
        document_version_id: str,
        source_sha256: str,
    ) -> bool: ...


@runtime_checkable
class DocumentParserPort(Protocol):
    def parse(self, version: DocumentVersion, content: bytes) -> DocumentParseSnapshot: ...


@runtime_checkable
class DocumentOcrPort(Protocol):
    def apply_ocr(
        self,
        version: DocumentVersion,
        content: bytes,
        snapshot: DocumentParseSnapshot,
    ) -> DocumentParseSnapshot: ...


@runtime_checkable
class EvidenceChunkerPort(Protocol):
    def chunk(
        self,
        owner_id: str,
        document_id: str,
        version: DocumentVersion,
        snapshot: DocumentParseSnapshot,
    ) -> list[EvidenceChunk]: ...


@runtime_checkable
class EvidenceIndexPort(Protocol):
    def replace_version_chunks(
        self,
        document_version_id: str,
        chunks: list[EvidenceChunk],
        embeddings: list[list[float]],
    ) -> None: ...

    def search(
        self,
        *,
        owner_id: str,
        query: str,
        query_embedding: list[float],
        top_k: int = 5,
        document_ids: list[str] | None = None,
        document_version_ids: list[str] | None = None,
    ) -> list[EvidenceSearchHit]: ...

    def count_version(self, document_version_id: str) -> int: ...

    def delete_version(self, document_version_id: str) -> int: ...


@runtime_checkable
class RunControlPort(Protocol):
    @property
    def run_id(self) -> str: ...

    def check(self, *, stage: str | None = None) -> Any: ...

    def before_model_call(self, *, operation: str, stage: str) -> Any: ...

    def before_tool_call(self, *, operation: str) -> Any: ...

    def call_model(
        self,
        *,
        operation: str,
        stage: str,
        invoke: Callable[[], T],
        retry: bool = False,
    ) -> T: ...

    def call_tool(
        self,
        *,
        operation: str,
        invoke: Callable[[], T],
        retry: bool = False,
    ) -> T: ...

    def wait_for_graph_event(self, invoke: Callable[[], T]) -> T: ...

    def wait(self, seconds: float) -> None: ...

    def record_model_usage(
        self,
        *,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> None: ...

    def record_retry(self, *, kind: Literal["model", "tool"]) -> None: ...


@runtime_checkable
class EmbedPort(Protocol):
    def embed(
        self,
        texts: list[str],
        *,
        runtime_control: RunControlPort | None = None,
    ) -> list[list[float]]: ...


@runtime_checkable
class ChatPort(Protocol):
    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_mode: bool = False,
        runtime_control: RunControlPort | None = None,
        operation: str = "chat",
    ) -> str: ...

    def chat_with_usage(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_mode: bool = False,
        runtime_control: RunControlPort | None = None,
        operation: str = "chat",
    ) -> ChatResponse: ...


@runtime_checkable
class TraceSpanPort(Protocol):
    def add_metadata(self, metadata: Mapping[str, Any]) -> None: ...


@runtime_checkable
class TracePort(Protocol):
    def span(
        self,
        name: str,
        *,
        run_type: Literal["chain", "llm", "tool", "retriever"] = "chain",
        metadata: Mapping[str, Any] | None = None,
    ) -> AbstractContextManager[TraceSpanPort]: ...


@runtime_checkable
class MetricsPort(Protocol):
    def observe_http(
        self,
        *,
        method: str,
        route: str,
        status_code: int,
        duration_seconds: float,
    ) -> None: ...

    def observe_agent_run(
        self,
        *,
        workflow: str,
        status: str,
        duration_seconds: float,
        token_usage: int,
        cost: float,
        refused: bool,
    ) -> None: ...

    def observe_tool(
        self,
        *,
        tool: str,
        status: str,
        duration_seconds: float,
        retry_count: int,
    ) -> None: ...

    def record_llm_usage(
        self,
        *,
        operation: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost: float,
    ) -> None: ...

    def record_citation_failure(self, *, workflow: str) -> None: ...

    def render(self) -> bytes: ...

    @property
    def content_type(self) -> str: ...


@runtime_checkable
class RetrievePort(Protocol):
    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        corpus: Literal["law", "user_docs"] = "law",
        owner_id: str | None = None,
        runtime_control: RunControlPort | None = None,
    ) -> list[Chunk]: ...


@runtime_checkable
class ClaimSupportVerifierPort(Protocol):
    def verify(
        self,
        claims: list[EvidenceQAClaim],
        citations: list[EvidenceQACitation],
        user_context: list[UserContextMessage] | None = None,
        runtime_control: RunControlPort | None = None,
    ) -> ClaimSupportResult: ...


@runtime_checkable
class EvidenceQAGeneratorPort(Protocol):
    def generate(
        self,
        *,
        question: str,
        citations: list[EvidenceQACitation],
        user_context: list[UserContextMessage] | None = None,
        preferences: list[str] | None = None,
        runtime_control: RunControlPort | None = None,
    ) -> EvidenceQADraft: ...


@runtime_checkable
class KbDocumentRepoPort(Protocol):
    def list_documents(
        self,
        *,
        owners: list[str | None] | None = None,
    ) -> list[KbDocument]: ...

    def get_document(
        self,
        source_name: str,
        *,
        owners: list[str | None] | None = None,
    ) -> KbDocument | None: ...

    def count_chunks(self) -> int: ...

    def delete_document(self, source_name: str, *, owner_id: Any = ...) -> int: ...

    def add_chunks(self, chunks: list[KbChunk], embeddings: list[list[float]]) -> None: ...


@runtime_checkable
class DocumentLoaderPort(Protocol):
    def load_file(
        self,
        file_path: str,
        *,
        original_filename: str | None = None,
        category: str | None = None,
        owner_id: str | None = None,
    ) -> list[KbChunk]: ...

    def load_web(
        self,
        url: str,
        *,
        category: str | None = None,
        owner_id: str | None = None,
    ) -> list[KbChunk]: ...


@runtime_checkable
class WebSearchPort(Protocol):
    def search(
        self,
        query: str,
        max_results: int = 3,
        *,
        runtime_control: RunControlPort | None = None,
    ) -> list[WebResult]: ...
