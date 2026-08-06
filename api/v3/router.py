"""V3 根路由。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

from api.v3.cases import build_case_routes
from api.v3.workspaces import build_workspace_routes

if TYPE_CHECKING:
    from app.container import AppContainer


def build_v3_router(container: AppContainer) -> APIRouter:
    root = APIRouter()
    root.include_router(build_workspace_routes(container))
    root.include_router(build_case_routes(container))
    return root
