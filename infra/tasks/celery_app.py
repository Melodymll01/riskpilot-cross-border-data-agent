"""Celery 应用配置；模块 import 不连接 Redis。"""

from __future__ import annotations

from celery import Celery

from config import Settings

DOCUMENT_TASK_NAME = "riskpilot.process_document"


def build_celery_app(settings: Settings) -> Celery:
    broker_url = settings.celery_broker_url or settings.redis_url or "memory://"
    app = Celery(
        "riskpilot",
        broker=broker_url,
        backend=settings.celery_result_backend,
        include=["infra.tasks.document_tasks"],
    )
    app.conf.update(
        task_default_queue=settings.celery_queue,
        task_routes={DOCUMENT_TASK_NAME: {"queue": settings.celery_queue}},
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        task_track_started=True,
        worker_prefetch_multiplier=1,
        task_soft_time_limit=settings.celery_soft_time_limit_seconds,
        task_time_limit=settings.celery_time_limit_seconds,
        broker_connection_retry_on_startup=True,
        result_expires=86400,
        timezone="UTC",
        enable_utc=True,
    )
    return app
