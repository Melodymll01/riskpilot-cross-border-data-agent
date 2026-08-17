"""V3 案件工作台静态契约测试。"""

from __future__ import annotations

import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


def test_case_workbench_dom_contract() -> None:
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    required_ids = {
        "nav-cases",
        "btn-demo-login",
        "case-pane",
        "case-load-form",
        "case-id-input",
        "case-workspace-select",
        "case-selector-list",
        "case-btn-new-workspace",
        "case-btn-new-case",
        "case-create-modal",
        "case-create-form",
        "case-create-kind",
        "case-create-name",
        "case-status",
        "case-btn-refresh",
        "case-btn-propose",
        "case-btn-continue",
        "case-btn-retry-run",
        "case-btn-cancel-run",
        "case-btn-review-run",
        "case-btn-reject-run",
        "case-upload-form",
        "case-upload-file",
        "case-upload-purpose",
        "case-upload-submit",
        "case-documents",
        "case-run",
        "case-run-plan",
        "case-run-timeline",
        "case-run-interrupt",
        "case-run-tools",
        "case-run-verification",
        "case-run-assessment",
        "case-missing-fields",
        "case-facts",
    }
    actual_ids = set(re.findall(r'\bid="([^"]+)"', html))
    assert required_ids <= actual_ids
    assert 'data-view="cases"' in html


def test_case_api_client_uses_v3_routes() -> None:
    source = (FRONTEND / "api.js").read_text(encoding="utf-8")
    assert 'const V3_BASE = "/api/v3"' in source
    for route in (
        "/workspaces",
        "/cases?workspace_id=",
        "/documents",
        "/processing-jobs/",
        "/parse",
        "/index",
        "/retry",
        "/fact-proposals",
        "/assessment-runs",
        "/transitions",
        "/continue",
        "/detail",
        "/cancel",
        "/review",
        "/events?after_sequence=0",
    ):
        assert route in source


def test_case_renderer_does_not_inject_untrusted_html() -> None:
    source = (FRONTEND / "cases.js").read_text(encoding="utf-8")
    assert ".innerHTML" not in source
    assert "document.createElement" in source
    assert ".textContent" in source
    assert "fact_confirmation_required" in source
    assert "missing_fact_fields" in source
    assert "createWorkspace" in source
    assert "loadWorkspaces" in source
    assert "loadCases" in source
    assert "openCreateModal" in source
    assert "uploadDocument" in source
    assert "runDocumentPipeline" in source
    assert "latest_job" in source
    assert "documentAction" in source
    assert "renderRunDetail" in source
    assert "renderTimeline" in source
    assert "renderToolCalls" in source
    assert "renderInterrupt" in source
    assert "renderAssessment" in source
    assert "rejectRun" in source
    assert 'current_stage !== "human_fact_confirmation"' in source
    assert "data-demo-case-id" in (FRONTEND / "index.html").read_text(encoding="utf-8")


def test_app_mounts_authenticated_case_view() -> None:
    source = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert 'import * as cases from "./cases.js"' in source
    assert 'view === "cases"' in source
    assert "cases.mount()" in source
    assert 'applyCaseGate(!!user && user.provider !== "anonymous")' in source
    assert '(!user || user.provider === "anonymous")' in source
    assert "isDemoLoginEnabled" in source
    assert "loginDemo" in source
