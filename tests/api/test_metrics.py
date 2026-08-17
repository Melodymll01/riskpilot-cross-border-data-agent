"""Prometheus endpoint 与 HTTP 低基数路由指标测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.container import AppContainer
from infra.observability import PrometheusMetricsAdapter
from tests.fakes import FakeTrace


def test_metrics_endpoint_exports_http_metric_and_is_not_in_openapi(
    client: TestClient,
) -> None:
    response = client.get("/api/v2/health/live")
    assert response.status_code == 200

    metrics = client.get("/api/v2/metrics")

    assert metrics.status_code == 200
    assert metrics.headers["content-type"].startswith("text/plain")
    assert "riskpilot_http_requests_total" in metrics.text
    assert 'route="/api/v2/health/live"' in metrics.text
    assert "/api/v2/metrics" not in client.get("/openapi.json").json()["paths"]


def test_http_observability_uses_route_template_not_resource_id(
    client: TestClient,
) -> None:
    container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
    trace = FakeTrace()
    metrics = PrometheusMetricsAdapter()
    container.trace = trace
    container.metrics = metrics
    sensitive_run_id = "run-sensitive-123"

    response = client.get(f"/api/v3/runs/{sensitive_run_id}")

    assert response.status_code == 401
    http_span = trace.spans[-1]
    assert http_span["name"] == "riskpilot.http.request"
    assert http_span["metadata"]["http.route"] == "/api/v3/runs/{run_id}"
    assert sensitive_run_id not in str(http_span["metadata"])
    payload = metrics.render().decode()
    assert 'route="/api/v3/runs/{run_id}"' in payload
    assert sensitive_run_id not in payload


def test_unmatched_path_uses_fixed_metric_label(client: TestClient) -> None:
    container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
    metrics = PrometheusMetricsAdapter()
    container.metrics = metrics

    response = client.get("/not-found/secret-resource-id")

    assert response.status_code == 404
    payload = metrics.render().decode()
    assert 'route="unmatched"' in payload
    assert "secret-resource-id" not in payload
