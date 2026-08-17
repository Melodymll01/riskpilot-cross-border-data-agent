"""Celery task failure finalization contract，不连接 Redis。"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

from domain import CaseDocument, Document, DocumentVersion, ProcessingJob
from infra.tasks.document_tasks import _mark_exhausted_failure
from tests.fakes import InMemoryDocumentRepo


def test_retry_exhaustion_marks_running_job_and_document_failed() -> None:
    repo = InMemoryDocumentRepo()
    version = DocumentVersion(
        version_id="ver_001",
        document_id="doc_001",
        version_number=1,
        object_key="objects/source.txt",
        sha256=hashlib.sha256(b"text").hexdigest(),
        mime_type="text/plain",
        size_bytes=4,
        created_at=100.0,
    )
    document = Document(
        document_id="doc_001",
        workspace_id="ws_001",
        logical_name="source.txt",
        document_type="case_material",
        status="embedding",
        created_by="github:alice",
        current_version_id=version.version_id,
        created_at=100.0,
        updated_at=103.0,
    )
    job = ProcessingJob(
        job_id="job_001",
        document_version_id=version.version_id,
        status="running",
        current_stage="embedding",
        progress=0.65,
        revision=3,
        created_at=100.0,
        updated_at=103.0,
        started_at=101.0,
    )
    repo.create_upload(
        document,
        version,
        CaseDocument(
            case_id="case_001",
            document_id=document.document_id,
            added_by="github:alice",
            added_at=100.0,
        ),
        job,
    )
    runtime = SimpleNamespace(document_repo=repo)

    _mark_exhausted_failure(runtime, job.job_id, ConnectionError("embedding unavailable"))

    failed_job = repo.get_job(job.job_id)
    failed_document = repo.get(document.document_id)
    assert failed_job is not None and failed_document is not None
    assert failed_job.status == "failed"
    assert failed_job.error_code == "TASK_CONNECTIONERROR"
    assert failed_document.status == "failed"
