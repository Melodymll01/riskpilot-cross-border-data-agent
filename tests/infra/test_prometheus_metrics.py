"""Prometheus 指标覆盖和标签基数契约。"""

from __future__ import annotations

from prometheus_client import CollectorRegistry

from domain import MetricsPort
from infra.observability import NoopMetricsAdapter, PrometheusMetricsAdapter


def test_prometheus_adapter_exports_all_phase8_metric_families() -> None:
    metrics = PrometheusMetricsAdapter(
        registry=CollectorRegistry(),
        cost_currency="CNY",
    )

    assert isinstance(metrics, MetricsPort)
    metrics.observe_http(
        method="GET",
        route="/api/v3/runs/{run_id}",
        status_code=200,
        duration_seconds=0.125,
    )
    metrics.observe_agent_run(
        workflow="case_assessment",
        status="interrupted",
        duration_seconds=1.5,
        token_usage=120,
        cost=0.001,
        refused=True,
    )
    metrics.observe_tool(
        tool="extract_fact_candidates",
        status="failed",
        duration_seconds=0.4,
        retry_count=2,
    )
    metrics.observe_worker_task(
        task="riskpilot.process_document",
        status="completed",
        duration_seconds=2.0,
        retry_count=1,
    )
    metrics.set_worker_queue_depth(queue="riskpilot.documents", depth=3)
    metrics.record_llm_usage(
        operation="extract_fact_candidates",
        model="glm-test",
        input_tokens=100,
        output_tokens=20,
        cost=0.00014,
    )
    metrics.record_citation_failure(workflow="case_assessment")

    payload = metrics.render().decode()

    assert 'riskpilot_http_requests_total{method="GET",route="/api/v3/runs/{run_id}"' in payload
    assert (
        'riskpilot_agent_runs_total{status="interrupted",workflow="case_assessment"} 1.0' in payload
    )
    assert 'riskpilot_agent_refusals_total{workflow="case_assessment"} 1.0' in payload
    assert (
        'riskpilot_tool_calls_total{status="failed",tool="extract_fact_candidates"} 1.0' in payload
    )
    assert 'riskpilot_tool_retries_total{tool="extract_fact_candidates"} 2.0' in payload
    assert (
        'riskpilot_worker_tasks_total{status="completed",task="riskpilot.process_document"} 1.0'
        in payload
    )
    assert 'riskpilot_worker_task_retries_total{task="riskpilot.process_document"} 1.0' in payload
    assert 'riskpilot_worker_queue_depth{queue="riskpilot.documents"} 3.0' in payload
    assert (
        'riskpilot_llm_tokens_total{model="glm-test",operation="extract_fact_candidates",'
        'token_type="input"} 100.0'
    ) in payload
    assert (
        'riskpilot_llm_tokens_total{model="glm-test",operation="extract_fact_candidates",'
        'token_type="output"} 20.0'
    ) in payload
    assert (
        'riskpilot_llm_estimated_cost_total{currency="CNY",model="glm-test",'
        'operation="extract_fact_candidates"} 0.00014'
    ) in payload
    assert (
        'riskpilot_citation_verification_failures_total{workflow="case_assessment"} 1.0'
    ) in payload


def test_noop_metrics_is_protocol_compatible_and_has_empty_payload() -> None:
    metrics = NoopMetricsAdapter()

    assert isinstance(metrics, MetricsPort)
    metrics.observe_http(
        method="GET",
        route="/health",
        status_code=200,
        duration_seconds=0.01,
    )
    assert metrics.render() == b""
