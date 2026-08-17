"""Celery 配置与 Dispatcher contract；不连接真实 Redis。"""

from __future__ import annotations

from unittest.mock import Mock

from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from config import Settings
from domain import BackgroundJobDispatcherPort
from infra.observability import OpenTelemetryTraceAdapter
from infra.tasks import (
    DOCUMENT_TASK_NAME,
    CeleryJobDispatcher,
    ManualJobDispatcher,
    build_celery_app,
)


def _settings() -> Settings:
    return Settings(
        llm_provider="local",
        embed_provider="local",
        celery_broker_url="redis://127.0.0.1:6379/0",
        celery_result_backend="redis://127.0.0.1:6379/1",
    )


def test_celery_app_has_reliable_json_only_configuration() -> None:
    settings = _settings()
    app = build_celery_app(settings)

    assert app.conf.task_serializer == "json"
    assert app.conf.accept_content == ["json"]
    assert app.conf.task_acks_late is True
    assert app.conf.task_reject_on_worker_lost is True
    assert app.conf.worker_prefetch_multiplier == 1
    assert app.conf.task_soft_time_limit == settings.celery_soft_time_limit_seconds
    assert app.conf.task_time_limit == settings.celery_time_limit_seconds
    assert app.conf.task_routes[DOCUMENT_TASK_NAME]["queue"] == settings.celery_queue


def test_celery_app_import_profile_uses_in_memory_broker_without_network() -> None:
    settings = Settings(llm_provider="local", embed_provider="local")

    app = build_celery_app(settings)

    assert app.conf.broker_url == "memory://"


def test_dispatcher_uses_attempt_scoped_task_id_and_cooperative_revoke() -> None:
    app = Mock()
    dispatcher = CeleryJobDispatcher(app)

    assert isinstance(dispatcher, BackgroundJobDispatcherPort)
    task_id = dispatcher.enqueue_document("job_001", attempt=3)
    dispatcher.cancel_document("job_001", attempt=3)

    assert task_id == "document:job_001:attempt3"
    app.send_task.assert_called_once_with(
        DOCUMENT_TASK_NAME,
        args=["job_001"],
        task_id=task_id,
        headers={},
    )
    app.control.revoke.assert_called_once_with(task_id, terminate=False)


def test_dispatcher_injects_current_w3c_trace_context() -> None:
    app = Mock()
    dispatcher = CeleryJobDispatcher(app)
    exporter = InMemorySpanExporter()
    trace = OpenTelemetryTraceAdapter(
        service_name="riskpilot-test",
        sampling_rate=1.0,
        exporter=exporter,
        hash_salt="test-observability-hash-salt",
    )

    with trace.span("riskpilot.http.request"):
        dispatcher.enqueue_document("job_001", attempt=1)

    trace.shutdown()
    parent = exporter.get_finished_spans()[0]
    headers = app.send_task.call_args.kwargs["headers"]
    traceparent = headers["traceparent"]
    assert traceparent.startswith(f"00-{parent.context.trace_id:032x}-")
    assert int(traceparent.rsplit("-", maxsplit=1)[1], 16) & 0x01 == 0x01


def test_manual_dispatcher_is_explicit_noop_profile() -> None:
    dispatcher = ManualJobDispatcher()

    assert isinstance(dispatcher, BackgroundJobDispatcherPort)
    assert dispatcher.enqueue_document("job_001", attempt=0) == "manual:job_001:attempt0"
    assert dispatcher.cancel_document("job_001", attempt=0) is None
