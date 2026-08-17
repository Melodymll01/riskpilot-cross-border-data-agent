"""RiskPilot V2 文档、版本与处理任务领域模型。"""

from __future__ import annotations

import re
import time
from typing import ClassVar, Literal

from pydantic import Field, model_validator

from domain.errors import InvalidDocumentTransition, InvalidProcessingJobTransition
from domain.models import BaseDomainModel

DocumentStatus = Literal[
    "uploaded",
    "queued",
    "parsing",
    "ocr",
    "chunking",
    "indexing",
    "ready",
    "failed",
    "deleted",
]
ProcessingJobStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
ProcessingStage = Literal[
    "validate",
    "persist",
    "extract_structure",
    "extract_text",
    "ocr",
    "extract_tables",
    "normalize",
    "chunk",
    "index_vector",
    "index_bm25",
    "quality_check",
    "ready",
]

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class Document(BaseDomainModel):
    """Workspace 内的逻辑文档，可拥有多个不可变版本。"""

    _ALLOWED_TRANSITIONS: ClassVar[dict[str, frozenset[str]]] = {
        "uploaded": frozenset({"queued", "deleted"}),
        "queued": frozenset({"parsing", "failed", "deleted"}),
        "parsing": frozenset({"ocr", "chunking", "failed", "deleted"}),
        "ocr": frozenset({"chunking", "failed", "deleted"}),
        "chunking": frozenset({"indexing", "failed", "deleted"}),
        "indexing": frozenset({"ready", "failed", "deleted"}),
        "ready": frozenset({"queued", "deleted"}),
        "failed": frozenset({"queued", "deleted"}),
        "deleted": frozenset(),
    }

    document_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    logical_name: str = Field(min_length=1, max_length=255)
    document_type: str = Field(min_length=1, max_length=100)
    status: DocumentStatus = "uploaded"
    created_by: str = Field(min_length=1)
    current_version_id: str | None = None
    created_at: float
    updated_at: float

    @model_validator(mode="after")
    def validate_document(self) -> Document:
        if not self.logical_name.strip():
            raise ValueError("logical_name 不能为空白字符串")
        if self.current_version_id is not None and not self.current_version_id.strip():
            raise ValueError("current_version_id 不能为空白字符串")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at 不能早于 created_at")
        return self

    def can_transition_to(self, target: DocumentStatus) -> bool:
        return target == self.status or target in self._ALLOWED_TRANSITIONS[self.status]

    def transition_to(
        self,
        target: DocumentStatus,
        *,
        at: float | None = None,
    ) -> Document:
        if target == self.status:
            return self
        if not self.can_transition_to(target):
            raise InvalidDocumentTransition(self.document_id, self.status, target)
        transition_time = time.time() if at is None else at
        if transition_time < self.updated_at:
            raise ValueError("文档状态变更时间不能早于更新时间")
        return self.model_copy(
            update={
                "status": target,
                "updated_at": transition_time,
            }
        )


class DocumentVersion(BaseDomainModel):
    """文档不可变版本及其原始对象引用。"""

    version_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    version_number: int = Field(ge=1)
    object_key: str = Field(min_length=1, max_length=1000)
    sha256: str = Field(min_length=64, max_length=64)
    mime_type: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0)
    parser_version: str = Field(default="", max_length=100)
    page_count: int | None = Field(default=None, ge=0)
    created_at: float

    @model_validator(mode="after")
    def validate_version(self) -> DocumentVersion:
        if not _SHA256_RE.fullmatch(self.sha256):
            raise ValueError("sha256 必须是 64 位小写十六进制")
        return self


class CaseDocument(BaseDomainModel):
    """Document 与 Case 的显式绑定关系。"""

    case_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    purpose: str = Field(default="", max_length=500)
    added_by: str = Field(min_length=1)
    added_at: float


class ProcessingJob(BaseDomainModel):
    """单个 DocumentVersion 的可重试处理任务。"""

    _ALLOWED_TRANSITIONS: ClassVar[dict[str, frozenset[str]]] = {
        "queued": frozenset({"running", "cancelled"}),
        "running": frozenset({"completed", "failed", "cancelled"}),
        "completed": frozenset(),
        "failed": frozenset({"queued"}),
        "cancelled": frozenset(),
    }

    job_id: str = Field(min_length=1)
    document_version_id: str = Field(min_length=1)
    status: ProcessingJobStatus = "queued"
    current_stage: ProcessingStage = "validate"
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    error_code: str | None = None
    error_message: str | None = None
    retry_count: int = Field(default=0, ge=0)
    created_at: float
    updated_at: float
    started_at: float | None = None
    completed_at: float | None = None

    @model_validator(mode="after")
    def validate_job(self) -> ProcessingJob:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at 不能早于 created_at")
        if self.started_at is not None and self.started_at < self.created_at:
            raise ValueError("started_at 不能早于 created_at")
        if self.completed_at is not None:
            lower_bound = self.started_at if self.started_at is not None else self.created_at
            if self.completed_at < lower_bound:
                raise ValueError("completed_at 不能早于任务开始时间")
        if self.status == "completed" and self.progress != 1.0:
            raise ValueError("completed 任务的 progress 必须为 1")
        if self.status == "running" and self.started_at is None:
            raise ValueError("running 任务必须记录 started_at")
        if self.status in {"completed", "failed", "cancelled"} and self.completed_at is None:
            raise ValueError("终态任务必须记录 completed_at")
        if self.status == "failed" and not self.error_code:
            raise ValueError("failed 任务必须记录 error_code")
        return self

    def start(
        self,
        *,
        stage: ProcessingStage = "validate",
        at: float | None = None,
    ) -> ProcessingJob:
        return self._transition(
            "running",
            at=at,
            current_stage=stage,
            started_at=time.time() if at is None else at,
            completed_at=None,
            error_code=None,
            error_message=None,
        )

    def advance(
        self,
        *,
        stage: ProcessingStage,
        progress: float,
        at: float | None = None,
    ) -> ProcessingJob:
        if self.status != "running":
            raise InvalidProcessingJobTransition(self.job_id, self.status, "running")
        if progress < self.progress:
            raise ValueError("处理进度不能倒退")
        if progress >= 1.0:
            raise ValueError("运行中任务的 progress 必须小于 1")
        update_time = time.time() if at is None else at
        if update_time < self.updated_at:
            raise ValueError("任务更新时间不能倒退")
        return self.model_copy(
            update={
                "current_stage": stage,
                "progress": progress,
                "updated_at": update_time,
            }
        )

    def complete(self, *, at: float | None = None) -> ProcessingJob:
        return self._transition(
            "completed",
            at=at,
            current_stage="ready",
            progress=1.0,
            completed_at=time.time() if at is None else at,
            error_code=None,
            error_message=None,
        )

    def fail(
        self,
        *,
        error_code: str,
        error_message: str = "",
        at: float | None = None,
    ) -> ProcessingJob:
        if not error_code:
            raise ValueError("error_code 必填")
        return self._transition(
            "failed",
            at=at,
            completed_at=time.time() if at is None else at,
            error_code=error_code,
            error_message=error_message or None,
        )

    def retry(self, *, at: float | None = None) -> ProcessingJob:
        return self._transition(
            "queued",
            at=at,
            current_stage="validate",
            progress=0.0,
            retry_count=self.retry_count + 1,
            started_at=None,
            completed_at=None,
            error_code=None,
            error_message=None,
        )

    def cancel(self, *, at: float | None = None) -> ProcessingJob:
        return self._transition(
            "cancelled",
            at=at,
            completed_at=time.time() if at is None else at,
        )

    def _transition(
        self,
        target: ProcessingJobStatus,
        *,
        at: float | None,
        **changes: object,
    ) -> ProcessingJob:
        if target == self.status:
            return self
        if target not in self._ALLOWED_TRANSITIONS[self.status]:
            raise InvalidProcessingJobTransition(self.job_id, self.status, target)
        transition_time = time.time() if at is None else at
        if transition_time < self.updated_at:
            raise ValueError("任务状态变更时间不能早于更新时间")
        return self.model_copy(
            update={
                **changes,
                "status": target,
                "updated_at": transition_time,
            }
        )
