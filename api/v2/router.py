"""``build_v2_router(container)`` —— 把 auth/tasks/copilot/health 合并成一个根 router。

调用方负责 ``app.include_router(router, prefix="/api/v2")``；如果调用方还希望
异常映射统一，应一并调用 ``install_exception_handlers(app)``。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

from api.v2.audit import build_audit_routes
from api.v2.auth import build_auth_routes
from api.v2.copilot import build_copilot_routes
from api.v2.documents import build_documents_routes
from api.v2.feedback import build_feedback_routes
from api.v2.health import build_health_routes
from api.v2.memory import build_memory_routes
from api.v2.tasks import build_task_routes

if TYPE_CHECKING:
    from api.v2.ratelimit import RateLimiter
    from app.container import AppContainer


def build_v2_router(
    container: AppContainer, *, limiter: RateLimiter | None = None
) -> APIRouter:
    """构造 v2 根 router；包含 auth/tasks/documents/audit/memory/copilot/health 全部子路由。

    ``limiter`` 为 ``None`` 时（如测试）所有限流依赖退化为无操作。
    """
    root = APIRouter()
    root.include_router(build_auth_routes(container, limiter=limiter))
    root.include_router(build_task_routes(container))
    root.include_router(build_feedback_routes(container))
    root.include_router(build_documents_routes(container, limiter=limiter))
    root.include_router(build_audit_routes(container))
    root.include_router(build_memory_routes(container))
    root.include_router(build_copilot_routes(container, limiter=limiter))
    root.include_router(build_health_routes(container))
    return root
