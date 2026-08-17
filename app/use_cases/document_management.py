"""V2 案件文档上传、查询与下载用例。"""

from __future__ import annotations

import hashlib
import io
import logging
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from domain.documents import CaseDocument, Document, DocumentVersion, ProcessingJob
from domain.errors import (
    CaseArchived,
    DocumentNotFound,
    DocumentTooLarge,
    InvalidDocumentContent,
    ProcessingJobNotFound,
    UnsupportedDocumentType,
    WorkspaceNotFound,
)
from domain.workspaces import WorkspaceRole

if TYPE_CHECKING:
    from app.use_cases.case_management import CaseManagementUseCase
    from app.use_cases.workspace_management import WorkspaceManagementUseCase
    from app.workers import (
        DocumentProcessingWorker,
        EvidenceIndexWorker,
        IndexStageResult,
        ParseStageResult,
    )
    from domain.ports import BackgroundJobDispatcherPort, DocumentRepoPort, ObjectStorePort

_SUPPORTED_SUFFIXES = {".pdf", ".docx", ".txt", ".md", ".markdown"}
_WRITE_ROLES: set[WorkspaceRole] = {"editor", "reviewer", "admin"}
_DOCX_REQUIRED_ENTRIES = {"[Content_Types].xml", "word/document.xml"}
_DOCX_MAX_ENTRIES = 2_000
_DOCX_MAX_COMPRESSION_RATIO = 100
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DocumentUploadResult:
    document: Document
    version: DocumentVersion
    binding: CaseDocument
    job: ProcessingJob


@dataclass(frozen=True)
class DocumentDetail:
    document: Document
    version: DocumentVersion
    binding: CaseDocument
    latest_job: ProcessingJob | None


@dataclass(frozen=True)
class CaseDocumentSummary:
    document: Document
    latest_job: ProcessingJob | None


@dataclass(frozen=True)
class DocumentDownload:
    filename: str
    mime_type: str
    content: bytes


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class DocumentManagementUseCase:
    """把原始文件安全持久化为案件证据，并创建待处理任务。"""

    def __init__(
        self,
        *,
        document_repo: DocumentRepoPort,
        object_store: ObjectStorePort,
        case_management: CaseManagementUseCase,
        workspace_management: WorkspaceManagementUseCase,
        max_upload_bytes: int,
        job_dispatcher: BackgroundJobDispatcherPort,
        processing_worker: DocumentProcessingWorker | None = None,
        index_worker: EvidenceIndexWorker | None = None,
    ) -> None:
        if max_upload_bytes < 1:
            raise ValueError("max_upload_bytes 必须大于 0")
        self._repo = document_repo
        self._objects = object_store
        self._case_management = case_management
        self._workspace_management = workspace_management
        self._max_upload_bytes = max_upload_bytes
        self._dispatcher = job_dispatcher
        self._processing_worker = processing_worker
        self._index_worker = index_worker

    def bind_processing_worker(self, worker: DocumentProcessingWorker) -> None:
        """容器完成 Worker 装配后绑定，避免构造期循环依赖。"""
        self._processing_worker = worker

    def bind_index_worker(self, worker: EvidenceIndexWorker) -> None:
        self._index_worker = worker

    def upload(
        self,
        actor_id: str,
        *,
        case_id: str,
        filename: str,
        content: bytes,
        document_type: str = "case_material",
        purpose: str = "",
    ) -> DocumentUploadResult:
        case = self._case_management.get_case(case_id, actor_id)
        if case.status == "archived":
            raise CaseArchived(case.case_id)
        allowed_roles: set[WorkspaceRole] = (
            {"admin"} if document_type == "workspace_knowledge" else _WRITE_ROLES
        )
        self._workspace_management.require_role(
            case.workspace_id,
            actor_id,
            allowed_roles,
            action="上传案件材料",
        )
        logical_name, suffix = _normalize_filename(filename)
        mime_type = _validate_content(
            suffix,
            content,
            max_upload_bytes=self._max_upload_bytes,
        )

        now = time.time()
        document_id = _new_id("doc")
        version_id = _new_id("ver")
        job_id = _new_id("job")
        object_key = str(
            PurePosixPath(
                case.workspace_id,
                document_id,
                version_id,
                f"source{suffix}",
            )
        )
        document = Document(
            document_id=document_id,
            workspace_id=case.workspace_id,
            logical_name=logical_name,
            document_type=document_type,
            status="queued",
            created_by=actor_id,
            current_version_id=version_id,
            created_at=now,
            updated_at=now,
        )
        version = DocumentVersion(
            version_id=version_id,
            document_id=document_id,
            version_number=1,
            object_key=object_key,
            sha256=hashlib.sha256(content).hexdigest(),
            mime_type=mime_type,
            size_bytes=len(content),
            created_at=now,
        )
        binding = CaseDocument(
            case_id=case.case_id,
            document_id=document_id,
            purpose=purpose,
            added_by=actor_id,
            added_at=now,
        )
        job = ProcessingJob(
            job_id=job_id,
            document_version_id=version_id,
            current_stage="extract_structure",
            created_at=now,
            updated_at=now,
        )

        self._objects.put(object_key, content)
        try:
            self._repo.create_upload(document, version, binding, job)
        except Exception:
            self._objects.delete(object_key)
            raise
        dispatched_job = self._enqueue_or_fail(job, document)
        dispatched_document = (
            document
            if dispatched_job.status == "queued"
            else self._repo.get(document.document_id) or document
        )

        return DocumentUploadResult(
            document=dispatched_document,
            version=version,
            binding=binding,
            job=dispatched_job,
        )

    def list_case_documents(
        self,
        case_id: str,
        actor_id: str,
    ) -> list[CaseDocumentSummary]:
        self._case_management.get_case(case_id, actor_id)
        documents = self._repo.list_for_case(case_id)
        return [
            CaseDocumentSummary(
                document=document,
                latest_job=self._get_latest_job(document),
            )
            for document in documents
        ]

    def get_detail(
        self,
        case_id: str,
        document_id: str,
        actor_id: str,
    ) -> DocumentDetail:
        self._case_management.get_case(case_id, actor_id)
        binding = self._repo.get_binding(case_id, document_id)
        document = self._repo.get(document_id)
        if binding is None or document is None or document.status == "deleted":
            raise DocumentNotFound(document_id)
        if document.current_version_id is None:
            raise InvalidDocumentContent("文档缺少当前版本")
        version = self._repo.get_version(document.current_version_id)
        if version is None:
            raise InvalidDocumentContent("文档当前版本不存在")
        return DocumentDetail(
            document=document,
            version=version,
            binding=binding,
            latest_job=self._repo.get_latest_job_for_version(version.version_id),
        )

    def download(
        self,
        case_id: str,
        document_id: str,
        actor_id: str,
    ) -> DocumentDownload:
        detail = self.get_detail(case_id, document_id, actor_id)
        return DocumentDownload(
            filename=detail.document.logical_name,
            mime_type=detail.version.mime_type,
            content=self._objects.read(detail.version.object_key),
        )

    def get_job(self, job_id: str, actor_id: str) -> ProcessingJob:
        job, _ = self._authorize_job(job_id, actor_id, write=False)
        return job

    def list_jobs(
        self,
        case_id: str,
        actor_id: str,
        *,
        statuses: set[str] | None = None,
        limit: int = 50,
    ) -> list[ProcessingJob]:
        self._case_management.get_case(case_id, actor_id)
        return self._repo.list_jobs_for_case(
            case_id,
            statuses=statuses,
            limit=limit,
        )

    def run_parse_stage(
        self,
        job_id: str,
        actor_id: str,
    ) -> ParseStageResult:
        self._authorize_job(job_id, actor_id, write=True)
        if self._processing_worker is None:
            raise RuntimeError("文档处理 Worker 尚未装配")
        return self._processing_worker.run_parse_stage(job_id)

    def retry_job(self, job_id: str, actor_id: str) -> ProcessingJob:
        job, document = self._authorize_job(job_id, actor_id, write=True)
        if job.status != "failed" or document.status != "failed":
            raise InvalidDocumentContent("只有 failed 文档处理任务可以重试")
        retry_at = max(time.time(), job.updated_at, document.updated_at)
        retried_job = job.retry(at=retry_at)
        queued_document = document.transition_to("queued", at=retried_job.updated_at)
        self._repo.update_processing_state(
            queued_document,
            retried_job,
            expected_revision=job.revision,
        )
        return self._enqueue_or_fail(retried_job, queued_document)

    def cancel_job(self, job_id: str, actor_id: str) -> ProcessingJob:
        job, document = self._authorize_job(job_id, actor_id, write=True)
        if job.status not in {"queued", "running"}:
            raise InvalidDocumentContent("只有 queued/running 文档处理任务可以取消")
        cancelled_at = max(time.time(), job.updated_at, document.updated_at)
        cancelled_job = job.cancel(at=cancelled_at)
        cancelled_document = document.transition_to("cancelled", at=cancelled_at)
        self._repo.update_processing_state(
            cancelled_document,
            cancelled_job,
            expected_revision=job.revision,
        )
        try:
            self._dispatcher.cancel_document(job.job_id, attempt=job.retry_count)
        except Exception:
            logger.warning("Celery revoke 失败，数据库 cancelled 状态仍然生效", exc_info=True)
        return cancelled_job

    def run_index_stage(
        self,
        job_id: str,
        actor_id: str,
    ) -> IndexStageResult:
        self._authorize_job(job_id, actor_id, write=True)
        if self._index_worker is None:
            raise RuntimeError("证据索引 Worker 尚未装配")
        return self._index_worker.run(job_id)

    def _authorize_job(
        self,
        job_id: str,
        actor_id: str,
        *,
        write: bool,
    ) -> tuple[ProcessingJob, Document]:
        job = self._repo.get_job(job_id)
        if job is None:
            raise ProcessingJobNotFound(job_id)
        version = self._repo.get_version(job.document_version_id)
        if version is None:
            raise ProcessingJobNotFound(job_id)
        document = self._repo.get(version.document_id)
        if document is None:
            raise ProcessingJobNotFound(job_id)
        try:
            if write:
                self._workspace_management.require_role(
                    document.workspace_id,
                    actor_id,
                    _WRITE_ROLES,
                    action="执行文档处理任务",
                )
            else:
                self._workspace_management.require_membership(
                    document.workspace_id,
                    actor_id,
                )
        except WorkspaceNotFound as exc:
            raise ProcessingJobNotFound(job_id) from exc
        return job, document

    def _get_latest_job(self, document: Document) -> ProcessingJob | None:
        if document.current_version_id is None:
            return None
        return self._repo.get_latest_job_for_version(document.current_version_id)

    def _enqueue_or_fail(
        self,
        job: ProcessingJob,
        document: Document,
    ) -> ProcessingJob:
        try:
            self._dispatcher.enqueue_document(job.job_id, attempt=job.retry_count)
        except Exception as exc:
            failed_at = max(time.time(), job.updated_at, document.updated_at)
            failed_job = job.fail(
                error_code="TASK_DISPATCH_FAILED",
                error_message=str(exc),
                at=failed_at,
            )
            failed_document = document.transition_to("failed", at=failed_at)
            self._repo.update_processing_state(
                failed_document,
                failed_job,
                expected_revision=job.revision,
            )
            return failed_job
        return job


def _normalize_filename(filename: str) -> tuple[str, str]:
    logical_name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not logical_name or logical_name in {".", ".."}:
        raise InvalidDocumentContent("文件名不能为空")
    suffix = PurePosixPath(logical_name).suffix.lower()
    if suffix not in _SUPPORTED_SUFFIXES:
        supported = ", ".join(sorted(_SUPPORTED_SUFFIXES))
        raise UnsupportedDocumentType(f"不支持的文件扩展名 {suffix or '<empty>'}；支持 {supported}")
    return logical_name, suffix


def _validate_content(
    suffix: str,
    content: bytes,
    *,
    max_upload_bytes: int,
) -> str:
    if not content:
        raise InvalidDocumentContent("文件内容不能为空")
    if len(content) > max_upload_bytes:
        raise DocumentTooLarge(f"文件超过 {max_upload_bytes} 字节限制")
    if suffix == ".pdf":
        if not content.startswith(b"%PDF-"):
            raise InvalidDocumentContent("PDF 文件头无效")
        return "application/pdf"
    if suffix == ".docx":
        return _validate_docx(content, max_upload_bytes=max_upload_bytes)
    return _validate_text(suffix, content)


def _validate_docx(content: bytes, *, max_upload_bytes: int) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > _DOCX_MAX_ENTRIES:
                raise InvalidDocumentContent("DOCX ZIP 条目数量异常")
            if any(info.flag_bits & 0x1 for info in entries):
                raise InvalidDocumentContent("DOCX 不允许包含加密 ZIP 条目")
            names = {info.filename for info in entries}
            if not _DOCX_REQUIRED_ENTRIES.issubset(names):
                raise InvalidDocumentContent("DOCX 缺少必要结构")
            if archive.testzip() is not None:
                raise InvalidDocumentContent("DOCX 压缩内容损坏")
            total_uncompressed = 0
            for info in entries:
                total_uncompressed += info.file_size
                if info.file_size > max_upload_bytes * 20:
                    raise InvalidDocumentContent("DOCX 单个 ZIP 条目解压体积异常")
                if info.file_size and info.compress_size == 0:
                    raise InvalidDocumentContent("DOCX ZIP 压缩信息异常")
                if (
                    info.compress_size > 0
                    and info.file_size / info.compress_size > _DOCX_MAX_COMPRESSION_RATIO
                ):
                    raise InvalidDocumentContent("DOCX ZIP 压缩比异常")
    except (zipfile.BadZipFile, OSError) as exc:
        raise InvalidDocumentContent("DOCX 文件结构无效") from exc
    if total_uncompressed > max_upload_bytes * 20:
        raise InvalidDocumentContent("DOCX 解压后体积异常")
    return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _validate_text(suffix: str, content: bytes) -> str:
    if b"\x00" in content:
        raise InvalidDocumentContent("文本文件包含二进制空字节")
    try:
        content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InvalidDocumentContent("文本文件必须使用 UTF-8 编码") from exc
    return "text/markdown" if suffix in {".md", ".markdown"} else "text/plain"
