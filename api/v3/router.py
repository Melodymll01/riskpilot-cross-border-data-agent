"""V3 根路由。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

from api.v3.assessment_runs import build_assessment_run_routes
from api.v3.assessments import build_assessment_routes
from api.v3.cases import build_case_routes
from api.v3.documents import build_document_routes
from api.v3.evidence import build_evidence_routes
from api.v3.facts import build_fact_routes
from api.v3.policies import build_policy_routes
from api.v3.qa import build_qa_routes
from api.v3.visual import build_visual_routes
from api.v3.workspaces import build_workspace_routes

if TYPE_CHECKING:
    from app.container import AppContainer


def build_v3_router(container: AppContainer) -> APIRouter:
    root = APIRouter()
    root.include_router(build_workspace_routes(container))
    root.include_router(build_case_routes(container))
    root.include_router(build_document_routes(container))
    root.include_router(build_evidence_routes(container))
    root.include_router(build_fact_routes(container))
    root.include_router(build_policy_routes(container))
    root.include_router(build_assessment_routes(container))
    root.include_router(build_assessment_run_routes(container))
    root.include_router(build_qa_routes(container))
    root.include_router(build_visual_routes(container))
    return root
