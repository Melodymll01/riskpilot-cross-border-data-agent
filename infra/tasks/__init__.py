"""后台任务适配器。"""

from infra.tasks.celery_app import DOCUMENT_TASK_NAME, build_celery_app
from infra.tasks.celery_dispatcher import CeleryJobDispatcher
from infra.tasks.manual_dispatcher import ManualJobDispatcher

__all__ = [
    "CeleryJobDispatcher",
    "DOCUMENT_TASK_NAME",
    "ManualJobDispatcher",
    "build_celery_app",
]
