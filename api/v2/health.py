"""``/api/v2/health/*``：v2 路由层的存活探针。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter

if TYPE_CHECKING:
    from app.container import AppContainer


def build_health_routes(container: AppContainer) -> APIRouter:
    router = APIRouter(prefix="/health", tags=["health"])

    @router.get("", summary="存活检查")
    def health() -> dict[str, Any]:
        return {"status": "ok", "version": "v2"}

    @router.get("/ready", summary="就绪检查：容器各 Port 装配完毕")
    def ready() -> dict[str, Any]:
        return {
            "status": "ok",
            "ports_loaded": {
                "user_repo": container.user_repo is not None,
                "task_repo": container.task_repo is not None,
                "chat": container.chat is not None,
                "embedder": container.embedder is not None,
                "retriever": container.retriever is not None,
                "web_search": container.web_search is not None,
                "evidence": container.evidence is not None,
                "auth": container.auth is not None,
            },
            "tools": container.copilot_agent.tool_names,
        }

    return router
