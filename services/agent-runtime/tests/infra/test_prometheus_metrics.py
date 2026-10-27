"""Prometheus 指标覆盖和标签基数契约。"""

from __future__ import annotations

from prometheus_client import CollectorRegistry

from domain.ports import MetricsPort
from infra.observability import NoopMetricsAdapter, PrometheusMetricsAdapter


def test_prometheus_adapter_exports_current_metric_families() -> None:
    metrics = PrometheusMetricsAdapter(
        registry=CollectorRegistry(),
        cost_currency="CNY",
    )

    assert isinstance(metrics, MetricsPort)
    metrics.observe_http(
        method="POST",
        route="/api/v2/copilot/chat",
        status_code=200,
        duration_seconds=0.125,
    )
    metrics.observe_agent_run(
        workflow="conversation",
        status="completed",
        duration_seconds=1.5,
        token_usage=120,
        cost=0.001,
        refused=True,
    )
    metrics.observe_tool(
        tool="search_law",
        status="failed",
        duration_seconds=0.4,
        retry_count=2,
    )
    metrics.record_llm_usage(
        operation="conversation",
        model="glm-test",
        input_tokens=100,
        output_tokens=20,
        cost=0.00014,
    )
    metrics.record_citation_failure(workflow="conversation")

    payload = metrics.render().decode()
    assert 'riskpilot_http_requests_total{method="POST",route="/api/v2/copilot/chat"' in payload
    assert 'riskpilot_agent_runs_total{status="completed",workflow="conversation"} 1.0' in payload
    assert 'riskpilot_agent_refusals_total{workflow="conversation"} 1.0' in payload
    assert 'riskpilot_tool_calls_total{status="failed",tool="search_law"} 1.0' in payload
    assert 'riskpilot_tool_retries_total{tool="search_law"} 2.0' in payload
    assert (
        'riskpilot_llm_tokens_total{model="glm-test",operation="conversation",'
        'token_type="input"} 100.0'
    ) in payload
    assert (
        'riskpilot_llm_tokens_total{model="glm-test",operation="conversation",'
        'token_type="output"} 20.0'
    ) in payload
    assert (
        'riskpilot_llm_estimated_cost_total{currency="CNY",model="glm-test",'
        'operation="conversation"} 0.00014'
    ) in payload
    assert (
        'riskpilot_citation_verification_failures_total{workflow="conversation"} 1.0'
    ) in payload
    assert "riskpilot_worker" not in payload


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
