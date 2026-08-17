"""Prometheus 指标适配器；使用私有 Registry 避免测试和多容器重复注册。"""

from __future__ import annotations

import os

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    multiprocess,
)


class NoopMetricsAdapter:
    content_type = "text/plain; version=0.0.4; charset=utf-8"

    def observe_http(self, **kwargs: object) -> None:
        del kwargs

    def observe_agent_run(self, **kwargs: object) -> None:
        del kwargs

    def observe_tool(self, **kwargs: object) -> None:
        del kwargs

    def observe_worker_task(self, **kwargs: object) -> None:
        del kwargs

    def set_worker_queue_depth(self, **kwargs: object) -> None:
        del kwargs

    def record_llm_usage(self, **kwargs: object) -> None:
        del kwargs

    def record_citation_failure(self, **kwargs: object) -> None:
        del kwargs

    def render(self) -> bytes:
        return b""


class PrometheusMetricsAdapter:
    def __init__(
        self,
        *,
        registry: CollectorRegistry | None = None,
        cost_currency: str = "unspecified",
    ) -> None:
        self._cost_currency = _label(cost_currency)
        metric_registry = registry or CollectorRegistry(auto_describe=True)
        multiprocess_dir = os.getenv("PROMETHEUS_MULTIPROC_DIR")
        if registry is None and multiprocess_dir:
            self._registry = CollectorRegistry()
            multiprocess.MultiProcessCollector(
                self._registry,
                path=multiprocess_dir,
            )
        else:
            self._registry = metric_registry
        self._http_requests = Counter(
            "riskpilot_http_requests_total",
            "HTTP requests",
            ("method", "route", "status_code"),
            registry=metric_registry,
        )
        self._http_duration = Histogram(
            "riskpilot_http_request_duration_seconds",
            "HTTP request latency",
            ("method", "route"),
            registry=metric_registry,
        )
        self._agent_runs = Counter(
            "riskpilot_agent_runs_total",
            "Agent execution segments",
            ("workflow", "status"),
            registry=metric_registry,
        )
        self._agent_duration = Histogram(
            "riskpilot_agent_run_duration_seconds",
            "Agent execution duration",
            ("workflow",),
            registry=metric_registry,
        )
        self._agent_refusals = Counter(
            "riskpilot_agent_refusals_total",
            "Agent safe refusals",
            ("workflow",),
            registry=metric_registry,
        )
        self._tools = Counter(
            "riskpilot_tool_calls_total",
            "Agent tool calls",
            ("tool", "status"),
            registry=metric_registry,
        )
        self._tool_duration = Histogram(
            "riskpilot_tool_duration_seconds",
            "Agent tool latency",
            ("tool",),
            registry=metric_registry,
        )
        self._tool_retries = Counter(
            "riskpilot_tool_retries_total",
            "Agent tool retry count",
            ("tool",),
            registry=metric_registry,
        )
        self._worker_tasks = Counter(
            "riskpilot_worker_tasks_total",
            "Celery worker task executions",
            ("task", "status"),
            registry=metric_registry,
        )
        self._worker_duration = Histogram(
            "riskpilot_worker_task_duration_seconds",
            "Celery worker task duration",
            ("task",),
            registry=metric_registry,
        )
        self._worker_retries = Counter(
            "riskpilot_worker_task_retries_total",
            "Celery retry count",
            ("task",),
            registry=metric_registry,
        )
        self._worker_queue_depth = Gauge(
            "riskpilot_worker_queue_depth",
            "Broker queue depth",
            ("queue",),
            registry=metric_registry,
            multiprocess_mode="livemostrecent",
        )
        self._llm_tokens = Counter(
            "riskpilot_llm_tokens_total",
            "LLM token usage",
            ("operation", "model", "token_type"),
            registry=metric_registry,
        )
        self._llm_cost = Counter(
            "riskpilot_llm_estimated_cost_total",
            "Estimated LLM cost using explicit deployment price table",
            ("operation", "model", "currency"),
            registry=metric_registry,
        )
        self._citation_failures = Counter(
            "riskpilot_citation_verification_failures_total",
            "Claim-Citation verification failures",
            ("workflow",),
            registry=metric_registry,
        )

    @property
    def content_type(self) -> str:
        return CONTENT_TYPE_LATEST

    def observe_http(
        self,
        *,
        method: str,
        route: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        method_label = _label(method)
        route_label = _label(route, max_length=160)
        status_label = str(status_code)
        self._http_requests.labels(method_label, route_label, status_label).inc()
        self._http_duration.labels(method_label, route_label).observe(max(0.0, duration_seconds))

    def observe_agent_run(
        self,
        *,
        workflow: str,
        status: str,
        duration_seconds: float,
        token_usage: int,
        cost: float,
        refused: bool,
    ) -> None:
        del token_usage, cost
        workflow_label = _label(workflow)
        self._agent_runs.labels(workflow_label, _label(status)).inc()
        self._agent_duration.labels(workflow_label).observe(max(0.0, duration_seconds))
        if refused:
            self._agent_refusals.labels(workflow_label).inc()

    def observe_tool(
        self,
        *,
        tool: str,
        status: str,
        duration_seconds: float,
        retry_count: int,
    ) -> None:
        tool_label = _label(tool)
        self._tools.labels(tool_label, _label(status)).inc()
        self._tool_duration.labels(tool_label).observe(max(0.0, duration_seconds))
        if retry_count:
            self._tool_retries.labels(tool_label).inc(retry_count)

    def observe_worker_task(
        self,
        *,
        task: str,
        status: str,
        duration_seconds: float,
        retry_count: int,
    ) -> None:
        task_label = _label(task)
        self._worker_tasks.labels(task_label, _label(status)).inc()
        self._worker_duration.labels(task_label).observe(max(0.0, duration_seconds))
        if retry_count:
            self._worker_retries.labels(task_label).inc(retry_count)

    def set_worker_queue_depth(self, *, queue: str, depth: int) -> None:
        self._worker_queue_depth.labels(_label(queue)).set(max(0, depth))

    def record_llm_usage(
        self,
        *,
        operation: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost: float,
    ) -> None:
        operation_label = _label(operation)
        model_label = _label(model)
        self._llm_tokens.labels(operation_label, model_label, "input").inc(max(0, input_tokens))
        self._llm_tokens.labels(operation_label, model_label, "output").inc(max(0, output_tokens))
        if cost > 0:
            self._llm_cost.labels(
                operation_label,
                model_label,
                self._cost_currency,
            ).inc(cost)

    def record_citation_failure(self, *, workflow: str) -> None:
        self._citation_failures.labels(_label(workflow)).inc()

    def render(self) -> bytes:
        return generate_latest(self._registry)


def _label(value: object, *, max_length: int = 100) -> str:
    text = str(value or "unknown").strip()
    return text[:max_length] or "unknown"
