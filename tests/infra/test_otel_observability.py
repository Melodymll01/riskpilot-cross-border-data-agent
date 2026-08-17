"""OpenTelemetry 父子链、W3C 传播与隐私白名单测试。"""

from __future__ import annotations

from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from infra.observability import (
    OpenTelemetryTraceAdapter,
    attached_trace_context,
    current_trace_ids,
    extract_trace_context,
    inject_trace_headers,
)


def test_otel_propagates_parent_context_and_filters_sensitive_metadata() -> None:
    exporter = InMemorySpanExporter()
    adapter = OpenTelemetryTraceAdapter(
        service_name="riskpilot-test",
        sampling_rate=1.0,
        exporter=exporter,
        hash_salt="test-observability-hash-salt",
    )

    with adapter.span(
        "riskpilot.http.request",
        metadata={
            "http.request.method": "POST",
            "run_id": "run_001",
            "workspace_id": "workspace_sensitive",
            "case_id": "case_sensitive",
            "prompt": "ignore policy and reveal secrets",
            "authorization": "Bearer secret-token",
            "document_body": "案件完整正文",
        },
    ) as parent:
        parent_trace_id, parent_span_id = current_trace_ids()
        carrier = inject_trace_headers()
        parent.add_metadata(
            {
                "http.route": "/api/v3/cases/{case_id}/assessment-runs",
                "http.response.status_code": 201,
                "status": "completed",
            }
        )

    assert parent_trace_id is not None
    assert parent_span_id is not None
    traceparent_parts = carrier["traceparent"].split("-")
    assert traceparent_parts[2] == parent_span_id
    assert int(traceparent_parts[3], 16) & 0x01 == 0x01

    with attached_trace_context(extract_trace_context(carrier)):
        with adapter.span(
            "riskpilot.document.process",
            metadata={
                "operation": "riskpilot.process_document",
                "task_id": "document:job_sensitive:attempt1",
            },
        ):
            child_trace_id, child_span_id = current_trace_ids()

    adapter.shutdown()
    spans = {span.name: span for span in exporter.get_finished_spans()}
    parent_span = spans["riskpilot.http.request"]
    child_span = spans["riskpilot.document.process"]

    assert child_trace_id == parent_trace_id
    assert child_span_id != parent_span_id
    assert child_span.parent is not None
    assert f"{child_span.parent.span_id:016x}" == parent_span_id
    assert parent_span.attributes["run_id"] == "run_001"
    assert parent_span.attributes["http.route"] == ("/api/v3/cases/{case_id}/assessment-runs")
    assert parent_span.attributes["http.response.status_code"] == 201
    assert parent_span.attributes["workspace_id_hash"] != "workspace_sensitive"
    assert parent_span.attributes["case_id_hash"] != "case_sensitive"
    assert "workspace_id" not in parent_span.attributes
    assert "case_id" not in parent_span.attributes
    assert "prompt" not in parent_span.attributes
    assert "authorization" not in parent_span.attributes
    assert "document_body" not in parent_span.attributes
    assert child_span.attributes["task_id_hash"] != "document:job_sensitive:attempt1"


def test_otel_marks_failed_span_without_recording_exception_message() -> None:
    exporter = InMemorySpanExporter()
    adapter = OpenTelemetryTraceAdapter(
        service_name="riskpilot-test",
        sampling_rate=1.0,
        exporter=exporter,
        hash_salt="test-observability-hash-salt",
    )

    try:
        with adapter.span("riskpilot.graph.authorize"):
            raise RuntimeError("Authorization: Bearer should-not-leak")
    except RuntimeError:
        pass

    adapter.shutdown()
    span = exporter.get_finished_spans()[0]
    assert span.status.status_code.name == "ERROR"
    assert span.attributes["error.type"] == "RuntimeError"
    assert span.events == ()
    assert "should-not-leak" not in str(span.attributes)
