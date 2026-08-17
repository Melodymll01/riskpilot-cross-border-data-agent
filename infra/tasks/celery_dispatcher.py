"""BackgroundJobDispatcherPort 的 Celery Adapter。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from infra.observability import inject_trace_headers
from infra.tasks.celery_app import DOCUMENT_TASK_NAME

if TYPE_CHECKING:
    from celery import Celery


class CeleryJobDispatcher:
    def __init__(self, app: Celery) -> None:
        self._app = app

    def enqueue_document(self, job_id: str, *, attempt: int) -> str:
        if not job_id:
            raise ValueError("job_id 不能为空")
        task_id = _task_id(job_id, attempt)
        self._app.send_task(
            DOCUMENT_TASK_NAME,
            args=[job_id],
            task_id=task_id,
            headers=inject_trace_headers(),
        )
        return task_id

    def cancel_document(self, job_id: str, *, attempt: int) -> None:
        if not job_id:
            raise ValueError("job_id 不能为空")
        self._app.control.revoke(_task_id(job_id, attempt), terminate=False)


def _task_id(job_id: str, attempt: int) -> str:
    if attempt < 0:
        raise ValueError("attempt 不能小于 0")
    return f"document:{job_id}:attempt{attempt}"
