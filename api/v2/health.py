"""``/api/v2/health/*``：v2 路由层的存活探针。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Response, status

if TYPE_CHECKING:
    from app.container import AppContainer


def build_health_routes(container: AppContainer) -> APIRouter:
    router = APIRouter(prefix="/health", tags=["health"])

    @router.get("", summary="存活检查")
    def health() -> dict[str, Any]:
        return {"status": "ok", "version": "v2"}

    @router.get("/live", summary="存活检查：只检查 API 进程")
    def live() -> dict[str, Any]:
        return {"status": "ok"}

    @router.get("/ready", summary="就绪检查：数据库和已启用基础设施")
    def ready(response: Response) -> dict[str, Any]:
        checks = container.readiness.check()
        is_ready = checks.get("ready") is True
        if not is_ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "ok" if is_ready else "not_ready",
            "checks": checks,
            "tools": container.copilot_agent.tool_names,
        }

    return router
