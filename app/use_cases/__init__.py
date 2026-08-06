"""use case 出口。"""

from app.use_cases.auth_login import AuthLoginUseCase
from app.use_cases.case_management import CaseManagementUseCase
from app.use_cases.document_management import (
    DocumentDetail,
    DocumentDownload,
    DocumentManagementUseCase,
    DocumentUploadResult,
)
from app.use_cases.evidence_search import EvidenceSearchUseCase
from app.use_cases.fact_management import (
    FactDetail,
    FactEvidenceInput,
    FactManagementUseCase,
)
from app.use_cases.ingest import IngestionUseCase
from app.use_cases.kb_management import KbIngestResult, KbManagementUseCase
from app.use_cases.run_query import RunQueryUseCase
from app.use_cases.task_management import TaskManagementUseCase
from app.use_cases.workspace_management import WorkspaceManagementUseCase

__all__ = [
    "AuthLoginUseCase",
    "CaseManagementUseCase",
    "DocumentDetail",
    "DocumentDownload",
    "DocumentManagementUseCase",
    "DocumentUploadResult",
    "EvidenceSearchUseCase",
    "FactDetail",
    "FactEvidenceInput",
    "FactManagementUseCase",
    "IngestionUseCase",
    "KbIngestResult",
    "KbManagementUseCase",
    "RunQueryUseCase",
    "TaskManagementUseCase",
    "WorkspaceManagementUseCase",
]
