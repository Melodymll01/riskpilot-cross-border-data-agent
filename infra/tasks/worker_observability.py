"""Celery prefork Worker 的 Prometheus exporter 和队列深度采样。"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, cast

from celery import signals
from prometheus_client import (
    CollectorRegistry,
    multiprocess,
    start_http_server,
)

from config import Settings

_metrics_server: Any | None = None


def _multiprocess_dir() -> Path | None:
    value = os.getenv("PROMETHEUS_MULTIPROC_DIR")
    return Path(value) if value else None


@signals.celeryd_init.connect
def initialize_metrics_directory(**_: object) -> None:
    if not Settings().prometheus_enabled:
        return
    directory = _multiprocess_dir()
    if directory is None:
        return
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True, exist_ok=True)


@signals.worker_ready.connect
def start_worker_metrics_server(**_: object) -> None:
    global _metrics_server
    settings = Settings()
    directory = _multiprocess_dir()
    if not settings.prometheus_enabled or directory is None or _metrics_server is not None:
        return
    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry, path=str(directory))
    _metrics_server = start_http_server(
        settings.prometheus_worker_port,
        addr="0.0.0.0",
        registry=registry,
    )


@signals.worker_process_shutdown.connect
def mark_worker_process_dead(pid: int | None = None, **_: object) -> None:
    directory = _multiprocess_dir()
    if pid is not None and directory is not None:
        multiprocess.mark_process_dead(pid, path=str(directory))


@signals.worker_shutdown.connect
def stop_worker_metrics_server(**_: object) -> None:
    global _metrics_server
    if _metrics_server is None:
        return
    server, thread = _metrics_server
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
    _metrics_server = None


def sample_queue_depth(
    *,
    metrics: Any,
    settings: Settings,
) -> None:
    broker_url = settings.celery_broker_url or settings.redis_url
    if not settings.prometheus_enabled or not broker_url:
        return
    try:
        import redis

        client = redis.Redis.from_url(
            broker_url,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
        depth = cast(int, client.llen(settings.celery_queue))
    except Exception:
        return
    metrics.set_worker_queue_depth(queue=settings.celery_queue, depth=depth)
