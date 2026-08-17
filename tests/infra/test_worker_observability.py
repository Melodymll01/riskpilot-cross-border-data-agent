"""Celery Worker Prometheus exporter 生命周期与故障降级测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from config import Settings
from infra.tasks import worker_observability


@pytest.fixture(autouse=True)
def reset_metrics_server() -> None:
    worker_observability._metrics_server = None
    yield
    worker_observability._metrics_server = None


def test_initialize_metrics_directory_clears_stale_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    directory = tmp_path / "prometheus"
    directory.mkdir()
    (directory / "stale.db").write_text("stale")
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(directory))
    monkeypatch.setenv("PROMETHEUS_ENABLED", "true")

    worker_observability.initialize_metrics_directory()

    assert directory.is_dir()
    assert list(directory.iterdir()) == []


def test_worker_metrics_server_starts_once_and_stops(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    directory = tmp_path / "prometheus"
    directory.mkdir()
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(directory))
    monkeypatch.setenv("PROMETHEUS_ENABLED", "true")
    monkeypatch.setenv("PROMETHEUS_WORKER_PORT", "19101")
    server = Mock()
    thread = Mock()
    start = Mock(return_value=(server, thread))
    monkeypatch.setattr(worker_observability, "start_http_server", start)

    worker_observability.start_worker_metrics_server()
    worker_observability.start_worker_metrics_server()
    worker_observability.stop_worker_metrics_server()

    start.assert_called_once()
    assert start.call_args.args == (19101,)
    assert start.call_args.kwargs["addr"] == "0.0.0.0"
    server.shutdown.assert_called_once_with()
    server.server_close.assert_called_once_with()
    thread.join.assert_called_once_with(timeout=5)


def test_worker_process_shutdown_marks_pid_dead(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))
    mark_dead = Mock()
    monkeypatch.setattr(worker_observability.multiprocess, "mark_process_dead", mark_dead)

    worker_observability.mark_worker_process_dead(pid=1234)

    mark_dead.assert_called_once_with(1234, path=str(tmp_path))


def test_queue_depth_uses_redis_llen_and_failures_are_non_blocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        prometheus_enabled=True,
        celery_broker_url="redis://127.0.0.1:6379/0",
        celery_queue="riskpilot.documents",
    )
    metrics = Mock()
    client = Mock()
    client.llen.return_value = 7

    import redis

    monkeypatch.setattr(redis.Redis, "from_url", Mock(return_value=client))
    worker_observability.sample_queue_depth(metrics=metrics, settings=settings)
    metrics.set_worker_queue_depth.assert_called_once_with(
        queue="riskpilot.documents",
        depth=7,
    )

    metrics.reset_mock()
    monkeypatch.setattr(
        redis.Redis,
        "from_url",
        Mock(side_effect=ConnectionError("redis unavailable")),
    )
    worker_observability.sample_queue_depth(metrics=metrics, settings=settings)
    metrics.set_worker_queue_depth.assert_not_called()
