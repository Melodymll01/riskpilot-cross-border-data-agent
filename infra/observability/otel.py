"""OpenTelemetry TracePort 适配器和 W3C context propagation。"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from typing import Any, Literal

from opentelemetry import propagate, trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
from opentelemetry.trace import SpanKind, Status, StatusCode

from domain.ports import TracePort, TraceSpanPort

_HASHED_ID_KEYS = {
    "actor_id",
    "assessment_id",
    "case_id",
    "owner_id",
    "task_id",
    "thread_id",
    "user_id",
    "workspace_id",
}
_STRING_ATTRIBUTE_KEYS = {
    "decision",
    "error_type",
    "evidence_state",
    "framework",
    "http.request.method",
    "http.route",
    "interrupt_kind",
    "langgraph_node",
    "model",
    "operation",
    "run_id",
    "stage",
    "status",
    "tool",
    "workflow",
}
_NUMBER_ATTRIBUTE_KEYS = {
    "attachment_count",
    "completed_stage_count",
    "cost",
    "document_count",
    "document_length",
    "duration_ms",
    "evidence_count",
    "http.response.status_code",
    "langgraph_step",
    "message_length",
    "missing_fact_count",
    "pending_document_count",
    "query_length",
    "ready_document_count",
    "retrieval_rounds",
    "retry_count",
    "target_length",
    "token_usage",
    "tool_count",
    "top_k",
}
_BOOLEAN_ATTRIBUTE_KEYS = {
    "completed",
    "enable_web_search",
    "has_attachments",
    "interrupted",
    "refused",
    "resumed",
    "risk_profile_configured",
    "web_search_used",
}


class _OpenTelemetryTraceSpan:
    def __init__(self, span: trace.Span, *, hash_salt: str) -> None:
        self._span = span
        self._hash_salt = hash_salt

    def add_metadata(self, metadata: Mapping[str, Any]) -> None:
        for key, value in _safe_attributes(
            metadata,
            hash_salt=self._hash_salt,
        ).items():
            self._span.set_attribute(key, value)


class OpenTelemetryTraceAdapter:
    """独立 TracerProvider；只有显式 endpoint 才创建 OTLP exporter。"""

    def __init__(
        self,
        *,
        service_name: str,
        sampling_rate: float,
        endpoint: str | None = None,
        exporter: SpanExporter | None = None,
        provider: TracerProvider | None = None,
        hash_salt: str = "dev-observability-salt-change-me",
    ) -> None:
        if not service_name.strip():
            raise ValueError("OTEL_SERVICE_NAME 必填")
        if not 0.0 <= sampling_rate <= 1.0:
            raise ValueError("OTEL_SAMPLING_RATE 必须在 0 到 1 之间")
        if len(hash_salt) < 16:
            raise ValueError("OpenTelemetry hash_salt 至少需要 16 个字符")
        self._hash_salt = hash_salt
        self._provider = provider or TracerProvider(
            resource=Resource.create({"service.name": service_name}),
            sampler=TraceIdRatioBased(sampling_rate),
            shutdown_on_exit=False,
        )
        selected_exporter = exporter
        if selected_exporter is None and endpoint:
            selected_exporter = OTLPSpanExporter(endpoint=endpoint)
        if selected_exporter is not None:
            self._provider.add_span_processor(BatchSpanProcessor(selected_exporter))
        self._tracer = self._provider.get_tracer("riskpilot")

    @contextmanager
    def span(
        self,
        name: str,
        *,
        run_type: Literal["chain", "llm", "tool", "retriever"] = "chain",
        metadata: Mapping[str, Any] | None = None,
    ) -> Iterator[TraceSpanPort]:
        kind = SpanKind.CLIENT if run_type in {"llm", "tool", "retriever"} else SpanKind.INTERNAL
        with self._tracer.start_as_current_span(
            name,
            kind=kind,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            span.set_attribute("riskpilot.run_type", run_type)
            safe_metadata = _safe_attributes(
                metadata or {},
                hash_salt=self._hash_salt,
            )
            for key, value in safe_metadata.items():
                span.set_attribute(key, value)
            try:
                yield _OpenTelemetryTraceSpan(span, hash_salt=self._hash_salt)
            except Exception as exc:
                span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                span.set_attribute("error.type", type(exc).__name__)
                raise

    def shutdown(self) -> None:
        self._provider.shutdown()


class CompositeTraceAdapter:
    def __init__(self, *adapters: TracePort) -> None:
        self._adapters = adapters

    @contextmanager
    def span(
        self,
        name: str,
        *,
        run_type: Literal["chain", "llm", "tool", "retriever"] = "chain",
        metadata: Mapping[str, Any] | None = None,
    ) -> Iterator[TraceSpanPort]:
        with ExitStack() as stack:
            spans = [
                stack.enter_context(adapter.span(name, run_type=run_type, metadata=metadata))
                for adapter in self._adapters
            ]
            yield _CompositeTraceSpan(spans)

    def shutdown(self) -> None:
        for adapter in self._adapters:
            shutdown = getattr(adapter, "shutdown", None)
            if callable(shutdown):
                shutdown()


class _CompositeTraceSpan:
    def __init__(self, spans: list[TraceSpanPort]) -> None:
        self._spans = spans

    def add_metadata(self, metadata: Mapping[str, Any]) -> None:
        for span in self._spans:
            span.add_metadata(metadata)


def inject_trace_headers(headers: dict[str, str] | None = None) -> dict[str, str]:
    carrier = dict(headers or {})
    propagate.inject(carrier)
    return carrier


def extract_trace_context(headers: Mapping[str, str] | None) -> Context:
    return propagate.extract(dict(headers or {}))


@contextmanager
def attached_trace_context(context: Context) -> Iterator[None]:
    token = __import__("opentelemetry.context", fromlist=["attach"]).attach(context)
    try:
        yield
    finally:
        __import__("opentelemetry.context", fromlist=["detach"]).detach(token)


def current_trace_ids() -> tuple[str | None, str | None]:
    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return None, None
    return f"{span_context.trace_id:032x}", f"{span_context.span_id:016x}"


def _safe_attributes(
    metadata: Mapping[str, Any],
    *,
    hash_salt: str,
) -> dict[str, str | int | float | bool]:
    safe: dict[str, str | int | float | bool] = {}
    for key, value in metadata.items():
        if key in _HASHED_ID_KEYS and isinstance(value, str) and value:
            safe[f"{key}_hash"] = hmac.new(
                hash_salt.encode("utf-8"),
                value.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()[:24]
            continue
        if (
            (key in _STRING_ATTRIBUTE_KEYS and isinstance(value, str) and 0 < len(value) <= 200)
            or (
                key in _NUMBER_ATTRIBUTE_KEYS
                and isinstance(value, (int, float))
                and not isinstance(value, bool)
            )
            or (key in _BOOLEAN_ATTRIBUTE_KEYS and isinstance(value, bool))
        ):
            safe[str(key)] = value
    return safe
