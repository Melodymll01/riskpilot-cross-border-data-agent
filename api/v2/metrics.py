"""Prometheus metrics endpoint。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter
from fastapi.responses import Response

if TYPE_CHECKING:
    from app.container import AppContainer


def build_metrics_routes(container: AppContainer) -> APIRouter:
    router = APIRouter(tags=["observability"])

    @router.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(
            content=container.metrics.render(),
            media_type=container.metrics.content_type,
        )

    return router
