"""幂等创建三类脱敏 Demo；默认使用真实 PostgreSQL/MinIO/Celery/Agent Graph。"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.container import AppContainer
from config import Settings
from domain.cases import Case
from domain.documents import CaseDocument, Document, DocumentVersion, ProcessingJob
from domain.facts import CaseFact, CaseFactEvidence
from domain.models import User
from domain.policies import PolicyRule
from domain.workspaces import Workspace, WorkspaceMembership

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

DEMO_WORKSPACE_ID = "ws_demo_cross_border"
DEMO_ADMIN_ID = "github:riskpilot-demo-admin"
DEMO_EDITOR_ID = "github:riskpilot-demo-editor"
DEMO_REVIEWER_ID = "github:riskpilot-demo-reviewer"
DEMO_RULE_ID = "DEMO-IMPORTANT-DATA-001"
DEMO_RULESET_VERSION = "demo-rules-v1"
DEMO_CASE_A_ID = "case_demo_happy_path"
DEMO_CASE_B_ID = "case_demo_human_loop"
DEMO_CASE_C_ID = "case_demo_failure_recovery"
DEMO_CASE_IDS = (DEMO_CASE_A_ID, DEMO_CASE_B_ID, DEMO_CASE_C_ID)

_FIXED_TIME = 1_783_036_800.0
_ASSESSMENT_DATE = date(2026, 7, 1)
_DOCUMENT_CONTENTS = {
    DEMO_CASE_A_ID: (
        "【合成演示材料】\n"
        "某企业计划向境外客服中心传输业务数据。经内部识别，本项目涉及重要数据。"
        "材料仅用于 RiskPilot 演示，不包含任何真实企业或个人信息。"
    ),
    DEMO_CASE_B_ID: (
        "【合成演示材料】\n"
        "某企业计划使用境外云服务处理运营数据，但现有材料没有说明是否涉及重要数据。"
        "该缺口必须由用户或 Reviewer 确认。"
    ),
    DEMO_CASE_C_ID: (
        "【合成演示材料】\n"
        "某企业计划向境外合作方提供数据。本任务被预置为 Embedding 失败，"
        "用于演示 ProcessingJob retry 与 Worker 恢复。"
    ),
}


@dataclass(frozen=True)
class DemoDocumentIds:
    document_id: str
    version_id: str
    job_id: str
    object_key: str


def _document_ids(case_id: str) -> DemoDocumentIds:
    suffix = case_id.removeprefix("case_demo_")
    return DemoDocumentIds(
        document_id=f"doc_demo_{suffix}",
        version_id=f"ver_demo_{suffix}",
        job_id=f"job_demo_{suffix}",
        object_key=f"{DEMO_WORKSPACE_ID}/demo/{suffix}/source.txt",
    )


def seed_demo(
    container: AppContainer,
    *,
    wait_timeout_seconds: float = 120.0,
    wait_for_ready: Callable[[AppContainer, Iterable[str], float], None] | None = None,
) -> dict[str, object]:
    _require_seed_profile(container.settings)
    _seed_users(container)
    _seed_workspace(container)
    _seed_rule(container)
    for case_id in DEMO_CASE_IDS:
        _seed_case(container, case_id)
    for case_id in (DEMO_CASE_A_ID, DEMO_CASE_B_ID):
        _seed_document(container, case_id, failed=False)
    _seed_document(container, DEMO_CASE_C_ID, failed=True)
    if wait_for_ready is None:
        _wait_for_ready_documents(
            container,
            (DEMO_CASE_A_ID, DEMO_CASE_B_ID),
            timeout_seconds=wait_timeout_seconds,
        )
    else:
        wait_for_ready(
            container,
            (DEMO_CASE_A_ID, DEMO_CASE_B_ID),
            wait_timeout_seconds,
        )
    _seed_happy_path_fact(container)
    run_a = _ensure_run(container, DEMO_CASE_A_ID)
    run_b = _ensure_run(container, DEMO_CASE_B_ID)
    result = {
        "workspace_id": DEMO_WORKSPACE_ID,
        "actor_ids": {
            "admin": DEMO_ADMIN_ID,
            "editor": DEMO_EDITOR_ID,
            "reviewer": DEMO_REVIEWER_ID,
        },
        "cases": {
            "happy_path": _case_summary(container, DEMO_CASE_A_ID, run_a),
            "human_in_the_loop": _case_summary(container, DEMO_CASE_B_ID, run_b),
            "failure_recovery": _case_summary(container, DEMO_CASE_C_ID, None),
        },
        "deterministic_profile": True,
        "warning": "确定性 Adapter 只用于演示工程协议，不代表真实模型效果。",
    }
    output_path = Path(container.settings.object_store_dir).parent / "demo-seed.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    result["local_manifest"] = str(output_path)
    return result


def _require_seed_profile(settings: Settings) -> None:
    errors: list[str] = []
    if settings.storage_backend != "postgres" or settings.vector_backend != "pgvector":
        errors.append("Seed Demo 必须使用 postgres + pgvector")
    if settings.task_backend != "celery":
        errors.append("Seed Demo 必须使用 Celery Worker")
    if settings.object_store_backend != "s3":
        errors.append("Seed Demo 必须使用 S3/MinIO")
    if settings.embed_provider != "deterministic":
        errors.append("Seed Demo 必须使用 deterministic embedding")
    if settings.agent_planner_backend != "deterministic":
        errors.append("Seed Demo 必须使用 deterministic planner")
    if settings.fact_proposal_backend != "safe_empty":
        errors.append("Seed Demo 必须使用 safe_empty Fact Proposal")
    if errors:
        raise RuntimeError("; ".join(errors))


def _seed_users(container: AppContainer) -> None:
    for user_id, display_name in (
        (DEMO_ADMIN_ID, "RiskPilot Demo Admin"),
        (DEMO_EDITOR_ID, "RiskPilot Demo Editor"),
        (DEMO_REVIEWER_ID, "RiskPilot Demo Reviewer"),
    ):
        existing = container.user_repo.get(user_id)
        created_at = existing.created_at if existing is not None else _FIXED_TIME
        container.user_repo.upsert(
            User(
                user_id=user_id,
                provider="github",
                provider_id=user_id.removeprefix("github:"),
                display_name=display_name,
                created_at=created_at,
                last_active_at=max(_FIXED_TIME, time.time()),
            )
        )


def _seed_workspace(container: AppContainer) -> None:
    workspace = container.workspace_repo.get(DEMO_WORKSPACE_ID)
    if workspace is None:
        workspace = Workspace(
            workspace_id=DEMO_WORKSPACE_ID,
            name="RiskPilot 脱敏演示工作区",
            created_by=DEMO_ADMIN_ID,
            created_at=_FIXED_TIME,
            updated_at=_FIXED_TIME,
        )
        container.workspace_repo.create(
            workspace,
            WorkspaceMembership(
                workspace_id=DEMO_WORKSPACE_ID,
                user_id=DEMO_ADMIN_ID,
                role="admin",
                joined_at=_FIXED_TIME,
            ),
        )
    for user_id, role in (
        (DEMO_ADMIN_ID, "admin"),
        (DEMO_EDITOR_ID, "editor"),
        (DEMO_REVIEWER_ID, "reviewer"),
    ):
        container.workspace_repo.upsert_membership(
            WorkspaceMembership(
                workspace_id=DEMO_WORKSPACE_ID,
                user_id=user_id,
                role=role,
                joined_at=_FIXED_TIME,
            )
        )


def _seed_rule(container: AppContainer) -> None:
    existing = container.policy_rule_repo.get(
        DEMO_WORKSPACE_ID,
        DEMO_RULE_ID,
        DEMO_RULESET_VERSION,
    )
    if existing is not None and existing.status == "published":
        return
    if existing is None:
        container.policy_management.create_rule(
            workspace_id=DEMO_WORKSPACE_ID,
            actor_id=DEMO_ADMIN_ID,
            rule=PolicyRule(
                workspace_id=DEMO_WORKSPACE_ID,
                rule_id=DEMO_RULE_ID,
                ruleset_version=DEMO_RULESET_VERSION,
                jurisdiction="CN",
                effective_from=date(2026, 1, 1),
                status="draft",
                required_fact_fields=["important_data_involved"],
                condition={
                    "field": "important_data_involved",
                    "operator": "eq",
                    "value": True,
                },
                result={
                    "risk_level": "high",
                    "candidate_path": "security_assessment",
                    "description": "合成演示规则：涉及重要数据时进入安全评估候选路径。",
                    "required_actions": ["补充并提交安全评估材料"],
                },
                source_clause_ids=["demo-clause-important-data"],
            ),
        )
    container.policy_management.publish_rule(
        DEMO_WORKSPACE_ID,
        DEMO_ADMIN_ID,
        rule_id=DEMO_RULE_ID,
        ruleset_version=DEMO_RULESET_VERSION,
    )


def _seed_case(container: AppContainer, case_id: str) -> None:
    if container.case_repo.get(case_id) is not None:
        return
    titles = {
        DEMO_CASE_A_ID: "Demo A：材料完整并等待 Reviewer",
        DEMO_CASE_B_ID: "Demo B：缺失关键事实并触发 HITL",
        DEMO_CASE_C_ID: "Demo C：Worker 失败后重试恢复",
    }
    statuses = {
        DEMO_CASE_A_ID: "ready_for_assessment",
        DEMO_CASE_B_ID: "ready_for_assessment",
        DEMO_CASE_C_ID: "processing_documents",
    }
    container.case_repo.create(
        Case(
            case_id=case_id,
            workspace_id=DEMO_WORKSPACE_ID,
            title=titles[case_id],
            description="完全合成的秋招演示案件，不含真实企业或个人数据。",
            jurisdiction="CN",
            scenario_type="synthetic_demo",
            assessment_date=_ASSESSMENT_DATE,
            status=statuses[case_id],
            owner_id=DEMO_EDITOR_ID,
            reviewer_id=DEMO_REVIEWER_ID,
            created_at=_FIXED_TIME,
            updated_at=_FIXED_TIME,
        )
    )


def _seed_document(
    container: AppContainer,
    case_id: str,
    *,
    failed: bool,
) -> None:
    ids = _document_ids(case_id)
    content = _DOCUMENT_CONTENTS[case_id].encode("utf-8")
    container.object_store.put(ids.object_key, content)
    existing_document = container.document_repo.get(ids.document_id)
    if existing_document is not None:
        existing_job = container.document_repo.get_job(ids.job_id)
        if not failed and existing_job is not None and existing_job.status == "queued":
            container.job_dispatcher.enqueue_document(
                existing_job.job_id,
                attempt=existing_job.retry_count,
            )
        return
    completed_at = _FIXED_TIME + 1 if failed else None
    document = Document(
        document_id=ids.document_id,
        workspace_id=DEMO_WORKSPACE_ID,
        logical_name=f"{case_id}.txt",
        document_type="case_material",
        status="failed" if failed else "queued",
        created_by=DEMO_EDITOR_ID,
        current_version_id=ids.version_id,
        created_at=_FIXED_TIME,
        updated_at=_FIXED_TIME + (1 if failed else 0),
    )
    version = DocumentVersion(
        version_id=ids.version_id,
        document_id=ids.document_id,
        version_number=1,
        object_key=ids.object_key,
        sha256=hashlib.sha256(content).hexdigest(),
        mime_type="text/plain",
        size_bytes=len(content),
        created_at=_FIXED_TIME,
    )
    binding = CaseDocument(
        case_id=case_id,
        document_id=ids.document_id,
        purpose="RiskPilot 合成演示材料",
        added_by=DEMO_EDITOR_ID,
        added_at=_FIXED_TIME,
    )
    job = ProcessingJob(
        job_id=ids.job_id,
        document_version_id=ids.version_id,
        status="failed" if failed else "queued",
        current_stage="embedding" if failed else "extract_structure",
        progress=0.65 if failed else 0.0,
        error_code="DEMO_EMBEDDING_UNAVAILABLE" if failed else None,
        error_message="合成故障：用于演示 Worker retry。" if failed else None,
        created_at=_FIXED_TIME,
        updated_at=_FIXED_TIME + (1 if failed else 0),
        started_at=_FIXED_TIME if failed else None,
        completed_at=completed_at,
    )
    container.document_repo.create_upload(document, version, binding, job)
    if not failed:
        container.job_dispatcher.enqueue_document(job.job_id, attempt=job.retry_count)


def _wait_for_ready_documents(
    container: AppContainer,
    case_ids: Iterable[str],
    *,
    timeout_seconds: float,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    pending = {_document_ids(case_id).document_id for case_id in case_ids}
    while pending and time.monotonic() < deadline:
        for document_id in list(pending):
            document = container.document_repo.get(document_id)
            if document is not None and document.status == "ready":
                pending.remove(document_id)
            elif document is not None and document.status == "failed":
                job = container.document_repo.get_latest_job_for_version(
                    document.current_version_id or ""
                )
                raise RuntimeError(
                    f"Demo 文档处理失败: {document_id} {None if job is None else job.error_code}"
                )
        if pending:
            time.sleep(0.5)
    if pending:
        raise TimeoutError("等待 Demo 文档完成超时: " + ", ".join(sorted(pending)))


def _seed_happy_path_fact(container: AppContainer) -> None:
    fact_id = "fact_demo_important_data"
    if container.case_fact_repo.get(fact_id) is not None:
        return
    ids = _document_ids(DEMO_CASE_A_ID)
    snapshot = container.document_repo.get_parse_snapshot(ids.version_id)
    if snapshot is None:
        raise RuntimeError("Demo A 缺少解析快照")
    quote = "本项目涉及重要数据"
    page_body = snapshot.pages[0].text
    if quote not in page_body:
        raise RuntimeError("Demo A 原文缺少固定 Fact quote")
    fact = CaseFact(
        fact_id=fact_id,
        case_id=DEMO_CASE_A_ID,
        field_name="important_data_involved",
        value=True,
        status="confirmed",
        source_type="document",
        confidence=1.0,
        criticality="critical",
        created_by=DEMO_EDITOR_ID,
        confirmed_by=DEMO_REVIEWER_ID,
        confirmed_at=_FIXED_TIME + 2,
        created_at=_FIXED_TIME + 2,
        updated_at=_FIXED_TIME + 2,
    )
    container.case_fact_repo.create(
        fact,
        [
            CaseFactEvidence(
                evidence_id="evidence_demo_important_data",
                case_id=DEMO_CASE_A_ID,
                fact_id=fact_id,
                fact_version=1,
                document_id=ids.document_id,
                document_version_id=ids.version_id,
                page_number=1,
                quote=quote,
                confidence=1.0,
                created_at=_FIXED_TIME + 2,
            )
        ],
    )


def _ensure_run(container: AppContainer, case_id: str) -> Any:
    existing = container.agent_run_repo.list_for_case(case_id, limit=10)
    if existing:
        latest = existing[0]
        if latest.status == "failed":
            return container.assessment_runs.retry_run(latest.run_id, DEMO_EDITOR_ID)
        if latest.status != "cancelled":
            return latest
    return container.assessment_runs.start(
        case_id,
        DEMO_EDITOR_ID,
        ruleset_version=DEMO_RULESET_VERSION,
        model_config_snapshot={"profile": "deterministic-seed-demo"},
    )


def _case_summary(container: AppContainer, case_id: str, run: Any | None) -> dict[str, object]:
    ids = _document_ids(case_id)
    case = container.case_repo.get(case_id)
    document = container.document_repo.get(ids.document_id)
    job = container.document_repo.get_job(ids.job_id)
    return {
        "case_id": case_id,
        "case_status": None if case is None else case.status,
        "document_id": ids.document_id,
        "document_status": None if document is None else document.status,
        "job_id": ids.job_id,
        "job_status": None if job is None else job.status,
        "run_id": None if run is None else run.run_id,
        "run_status": None if run is None else run.status,
        "current_stage": None if run is None else run.current_stage,
    }


def _close_container(container: AppContainer) -> None:
    close_runtime = getattr(container.workflow_runtime, "close", None)
    if callable(close_runtime):
        close_runtime()
    shutdown = getattr(container.trace, "shutdown", None)
    if callable(shutdown):
        shutdown()
    if container.storage_database is not None:
        container.storage_database.dispose()


def main() -> int:
    settings = Settings()
    settings.validate_runtime_configuration()
    container = AppContainer(settings)
    try:
        result = seed_demo(
            container,
            wait_timeout_seconds=float(os.getenv("SEED_WAIT_TIMEOUT_SECONDS", "120")),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        _close_container(container)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
