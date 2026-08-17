"""Phase 5 真实 PostgreSQL LangGraph checkpoint 恢复 contract。

只允许显式运行，不进入默认 pytest；使用 Deterministic Planner，不调用真实模型。
"""

# ruff: noqa: E402

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import Settings
from domain import CaseDocumentReadiness
from infra.workflows import LangGraphWorkflowRuntime


def main() -> int:
    if os.getenv("RUN_PHASE5_CONTRACT") != "1":
        raise RuntimeError("必须显式设置 RUN_PHASE5_CONTRACT=1")
    settings = Settings()
    if settings.storage_backend != "postgres":
        raise RuntimeError("Phase 5 contract 必须设置 STORAGE_BACKEND=postgres")
    suffix = uuid.uuid4().hex[:8]
    thread_id = f"phase5-contract-{suffix}"
    runtime_kwargs = {
        "checkpoint_db_path": settings.langgraph_checkpoint_db_path,
        "checkpoint_backend": "postgres",
        "database_url": settings.database_url,
    }

    first_runtime = LangGraphWorkflowRuntime(**runtime_kwargs)
    try:
        first = first_runtime.start_case_assessment(
            thread_id=thread_id,
            run_id=f"run_contract_{suffix}",
            case_id=f"case_contract_{suffix}",
            workspace_id=f"ws_contract_{suffix}",
            actor_id="contract:editor",
            actor_role="editor",
            ruleset_version="synthetic-v1",
            document_readiness=CaseDocumentReadiness(
                pending_document_ids=[f"doc_contract_{suffix}"]
            ),
            missing_fact_fields=["important_data_involved"],
            required_fact_fields=["important_data_involved"],
        )
        if first.stage != "inspect_documents":
            raise AssertionError(f"首次执行未停在文档中断: {first.model_dump()}")
    finally:
        first_runtime.close()

    second_runtime = LangGraphWorkflowRuntime(**runtime_kwargs)
    try:
        inspected = second_runtime.inspect_case_assessment(thread_id=thread_id)
        if inspected is None or inspected.checkpoint_id != first.checkpoint_id:
            raise AssertionError("Runtime 重建后未读取到同一 PostgreSQL checkpoint")
        resumed = second_runtime.resume_case_assessment(
            thread_id=thread_id,
            resume_value={"action": "retry"},
            state_update={
                "ready_document_ids": [f"doc_contract_{suffix}"],
                "pending_document_ids": [],
            },
            actor_id="contract:editor",
            actor_role="editor",
        )
        if resumed.stage != "human_fact_confirmation":
            raise AssertionError(f"恢复后未进入事实确认中断: {resumed.model_dump()}")
        print(
            {
                "thread_id": thread_id,
                "first_checkpoint_id": first.checkpoint_id,
                "resumed_checkpoint_id": resumed.checkpoint_id,
                "first_stage": first.stage,
                "resumed_stage": resumed.stage,
                "tool_calls": resumed.state["budget"]["tool_calls"],
            }
        )
    finally:
        second_runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
