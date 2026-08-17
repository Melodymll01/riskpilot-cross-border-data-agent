"""Phase 4 真实 Redis/Celery/PostgreSQL/MinIO contract。

只允许显式运行，不进入默认 pytest。使用 deterministic embedding 验证协议，不代表模型效果。
"""

# ruff: noqa: E402

from __future__ import annotations

import hashlib
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.factories import (
    build_job_dispatcher,
    build_object_store,
    build_sqlalchemy_database,
)
from config import Settings
from domain import (
    Case,
    CaseDocument,
    Document,
    DocumentVersion,
    ProcessingJob,
    Workspace,
    WorkspaceMembership,
)
from infra.storage.sqlalchemy import (
    SqlAlchemyCaseRepo,
    SqlAlchemyDocumentRepo,
    SqlAlchemyEvidenceIndex,
    SqlAlchemyWorkspaceRepo,
)


def main() -> int:
    if os.getenv("RUN_PHASE4_CONTRACT") != "1":
        raise RuntimeError("必须显式设置 RUN_PHASE4_CONTRACT=1")
    settings = Settings()
    settings.validate_runtime_configuration()
    database = build_sqlalchemy_database(settings)
    workspace_repo = SqlAlchemyWorkspaceRepo(database)
    case_repo = SqlAlchemyCaseRepo(database)
    document_repo = SqlAlchemyDocumentRepo(database)
    evidence_index = SqlAlchemyEvidenceIndex(
        database,
        embedding_dimensions=settings.embedding_dimensions,
    )
    object_store = build_object_store(settings)
    dispatcher = build_job_dispatcher(settings)

    suffix = uuid.uuid4().hex[:8]
    now = time.time()
    workspace_id = f"ws_contract_{suffix}"
    case_id = f"case_contract_{suffix}"
    document_id = f"doc_contract_{suffix}"
    version_id = f"ver_contract_{suffix}"
    job_id = f"job_contract_{suffix}"
    object_key = f"{workspace_id}/{document_id}/{version_id}/source.txt"
    content = "境外接收方应承担个人信息保护责任。".encode()

    workspace_repo.create(
        Workspace(
            workspace_id=workspace_id,
            name="Phase 4 Contract",
            created_by="contract:runner",
            created_at=now,
            updated_at=now,
        ),
        WorkspaceMembership(
            workspace_id=workspace_id,
            user_id="contract:runner",
            role="admin",
            joined_at=now,
        ),
    )
    case_repo.create(
        Case(
            case_id=case_id,
            workspace_id=workspace_id,
            title="Celery contract",
            owner_id="contract:runner",
            created_at=now,
            updated_at=now,
        )
    )
    object_store.put(object_key, content)
    document_repo.create_upload(
        Document(
            document_id=document_id,
            workspace_id=workspace_id,
            logical_name="contract.txt",
            document_type="case_material",
            status="queued",
            created_by="contract:runner",
            current_version_id=version_id,
            created_at=now,
            updated_at=now,
        ),
        DocumentVersion(
            version_id=version_id,
            document_id=document_id,
            version_number=1,
            object_key=object_key,
            sha256=hashlib.sha256(content).hexdigest(),
            mime_type="text/plain",
            size_bytes=len(content),
            created_at=now,
        ),
        CaseDocument(
            case_id=case_id,
            document_id=document_id,
            added_by="contract:runner",
            added_at=now,
        ),
        ProcessingJob(
            job_id=job_id,
            document_version_id=version_id,
            current_stage="extract_structure",
            created_at=now,
            updated_at=now,
        ),
    )
    task_id = dispatcher.enqueue_document(job_id, attempt=0)
    if os.getenv("PHASE4_CONTRACT_ENQUEUE_ONLY") == "1":
        print(
            {
                "job_id": job_id,
                "task_id": task_id,
                "status": "queued",
                "mode": "enqueue_only",
            },
            flush=True,
        )
        database.dispose()
        return 0
    duplicate_task_id = dispatcher.enqueue_document(job_id, attempt=0)
    if duplicate_task_id != task_id:
        raise AssertionError("同一 attempt 必须使用相同 task_id")

    deadline = time.time() + 60
    latest = document_repo.get_job(job_id)
    while latest is not None and latest.status not in {"completed", "failed", "cancelled"}:
        if time.time() >= deadline:
            raise TimeoutError(f"Job 未在期限内完成: {latest.model_dump()}")
        time.sleep(0.5)
        latest = document_repo.get_job(job_id)
    if latest is None or latest.status != "completed":
        raise AssertionError(f"Job 未成功完成: {latest}")
    if evidence_index.count_version(version_id) != 1:
        raise AssertionError("重复投递产生了非预期索引数量")
    print(
        {
            "job_id": job_id,
            "task_id": task_id,
            "status": latest.status,
            "revision": latest.revision,
            "chunk_count": evidence_index.count_version(version_id),
        }
    )
    database.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
