"""文档处理 Celery task。"""

from __future__ import annotations

import random
import time
from functools import lru_cache
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded

from config import Settings
from domain.errors import (
    InvalidDocumentContent,
    ProcessingJobConflict,
    ProcessingJobNotFound,
    UnsupportedDocumentType,
)
from infra.observability import attached_trace_context, extract_trace_context
from infra.tasks.celery_app import DOCUMENT_TASK_NAME, build_celery_app
from infra.tasks.runtime import WorkerRuntime, build_worker_runtime
from infra.tasks.worker_observability import sample_queue_depth

settings = Settings()
celery_app = build_celery_app(settings)


@lru_cache(maxsize=1)
def _runtime() -> WorkerRuntime:
    return build_worker_runtime(settings)


@celery_app.task(
    bind=True,
    name=DOCUMENT_TASK_NAME,
    acks_late=True,
    reject_on_worker_lost=True,
)
def process_document(self: Any, job_id: str) -> dict[str, str]:
    runtime = _runtime()
    headers = getattr(self.request, "headers", {}) or {}
    trace_context = extract_trace_context(headers)
    started = time.perf_counter()
    retries = int(getattr(self.request, "retries", 0))
    metric_retry_count = retries
    status = "failed"
    sample_queue_depth(metrics=runtime.metrics, settings=settings)
    try:
        with (
            attached_trace_context(trace_context),
            runtime.trace.span(
                "riskpilot.document.process",
                metadata={
                    "operation": DOCUMENT_TASK_NAME,
                    "task_id": getattr(self.request, "id", ""),
                    "retry_count": retries,
                },
            ) as span,
        ):
            result = runtime.pipeline.run(job_id)
            status = result.outcome
            span.add_metadata(
                {
                    "status": result.outcome,
                    "stage": result.stage,
                    "duration_ms": (time.perf_counter() - started) * 1000,
                }
            )
            return {
                "job_id": result.job_id,
                "outcome": result.outcome,
                "stage": result.stage,
            }
    except ProcessingJobConflict:
        latest = runtime.document_repo.get_job(job_id)
        if latest is None:
            raise ProcessingJobNotFound(job_id) from None
        outcome = (
            latest.status
            if latest.status in {"completed", "cancelled", "failed"}
            else "in_progress"
        )
        status = outcome
        return {
            "job_id": job_id,
            "outcome": outcome,
            "stage": latest.current_stage,
        }
    except (
        FileNotFoundError,
        InvalidDocumentContent,
        KeyError,
        UnsupportedDocumentType,
        ProcessingJobNotFound,
    ) as exc:
        _mark_exhausted_failure(runtime, job_id, exc)
        raise
    except SoftTimeLimitExceeded as exc:
        _mark_exhausted_failure(runtime, job_id, exc)
        raise
    except Exception as exc:
        if retries >= settings.celery_max_retries:
            _mark_exhausted_failure(runtime, job_id, exc)
            raise
        status = "retrying"
        metric_retry_count = retries + 1
        delay = min(
            settings.celery_retry_backoff_max_seconds,
            2**retries + random.randint(0, 2),
        )
        raise self.retry(
            exc=exc,
            countdown=delay,
            max_retries=settings.celery_max_retries,
        ) from exc
    finally:
        runtime.metrics.observe_worker_task(
            task=DOCUMENT_TASK_NAME,
            status=status,
            duration_seconds=time.perf_counter() - started,
            retry_count=metric_retry_count,
        )
        sample_queue_depth(metrics=runtime.metrics, settings=settings)


def _mark_exhausted_failure(
    runtime: WorkerRuntime,
    job_id: str,
    exc: Exception,
) -> None:
    job = runtime.document_repo.get_job(job_id)
    if job is None or job.status in {"completed", "failed", "cancelled"}:
        return
    version = runtime.document_repo.get_version(job.document_version_id)
    document = None if version is None else runtime.document_repo.get(version.document_id)
    if document is None:
        return
    failed_at = max(job.updated_at, document.updated_at)
    failed_job = job.fail(
        error_code=f"TASK_{type(exc).__name__.upper()}",
        error_message=str(exc),
        at=failed_at,
    )
    failed_document = document.transition_to("failed", at=failed_at)
    runtime.document_repo.update_processing_state(
        failed_document,
        failed_job,
        expected_revision=job.revision,
    )
