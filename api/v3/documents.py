"""V3 案件文档上传、下载与处理任务路由。"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import quote

import anyio
from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from fastapi.responses import Response

from api.v2.deps import make_require_owner
from api.v3.schemas import (
    DocumentDetailResponse,
    DocumentListResponse,
    DocumentOut,
    DocumentUploadResponse,
    DocumentVersionOut,
    ProcessingJobOut,
)
from domain.errors import DocumentTooLarge

if TYPE_CHECKING:
    from app.container import AppContainer
    from app.use_cases import DocumentDetail, DocumentUploadResult
    from domain.documents import Document, DocumentVersion, ProcessingJob


def _to_document_out(document: Document) -> DocumentOut:
    return DocumentOut(
        document_id=document.document_id,
        workspace_id=document.workspace_id,
        logical_name=document.logical_name,
        document_type=document.document_type,
        status=document.status,
        created_by=document.created_by,
        current_version_id=document.current_version_id,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


def _to_version_out(version: DocumentVersion) -> DocumentVersionOut:
    return DocumentVersionOut(
        version_id=version.version_id,
        document_id=version.document_id,
        version_number=version.version_number,
        sha256=version.sha256,
        mime_type=version.mime_type,
        size_bytes=version.size_bytes,
        parser_version=version.parser_version,
        page_count=version.page_count,
        created_at=version.created_at,
    )


def _to_job_out(job: ProcessingJob) -> ProcessingJobOut:
    return ProcessingJobOut(
        job_id=job.job_id,
        document_version_id=job.document_version_id,
        status=job.status,
        current_stage=job.current_stage,
        progress=job.progress,
        error_code=job.error_code,
        error_message=job.error_message,
        retry_count=job.retry_count,
        created_at=job.created_at,
        updated_at=job.updated_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


def _to_upload_response(result: DocumentUploadResult) -> DocumentUploadResponse:
    return DocumentUploadResponse(
        document=_to_document_out(result.document),
        version=_to_version_out(result.version),
        job=_to_job_out(result.job),
        purpose=result.binding.purpose,
    )


def _to_detail_response(detail: DocumentDetail) -> DocumentDetailResponse:
    return DocumentDetailResponse(
        document=_to_document_out(detail.document),
        version=_to_version_out(detail.version),
        purpose=detail.binding.purpose,
    )


def build_document_routes(container: AppContainer) -> APIRouter:
    router = APIRouter(tags=["v3-documents"])
    require_owner = make_require_owner(container)
    max_upload_bytes = container.settings.max_upload_mb * 1024 * 1024

    @router.post(
        "/cases/{case_id}/documents",
        response_model=DocumentUploadResponse,
        status_code=status.HTTP_202_ACCEPTED,
        summary="上传案件材料并创建异步处理任务",
    )
    async def upload_document(
        case_id: str,
        file: UploadFile = File(...),
        document_type: str = Form(default="case_material", max_length=100),
        purpose: str = Form(default="", max_length=500),
        actor_id: str = Depends(require_owner),
    ) -> DocumentUploadResponse:
        content = await file.read(max_upload_bytes + 1)
        if len(content) > max_upload_bytes:
            raise DocumentTooLarge(f"文件超过 {max_upload_bytes} 字节限制")
        result = await anyio.to_thread.run_sync(
            lambda: container.document_management.upload(
                actor_id,
                case_id=case_id,
                filename=file.filename or "",
                content=content,
                document_type=document_type,
                purpose=purpose,
            )
        )
        return _to_upload_response(result)

    @router.get(
        "/cases/{case_id}/documents",
        response_model=DocumentListResponse,
        summary="列出案件材料",
    )
    def list_documents(
        case_id: str,
        actor_id: str = Depends(require_owner),
    ) -> DocumentListResponse:
        documents = container.document_management.list_case_documents(
            case_id,
            actor_id,
        )
        return DocumentListResponse(
            documents=[_to_document_out(document) for document in documents]
        )

    @router.get(
        "/cases/{case_id}/documents/{document_id}",
        response_model=DocumentDetailResponse,
        summary="获取案件材料详情和当前版本",
    )
    def get_document(
        case_id: str,
        document_id: str,
        actor_id: str = Depends(require_owner),
    ) -> DocumentDetailResponse:
        detail = container.document_management.get_detail(
            case_id,
            document_id,
            actor_id,
        )
        return _to_detail_response(detail)

    @router.get(
        "/cases/{case_id}/documents/{document_id}/content",
        summary="下载案件材料原件",
    )
    def download_document(
        case_id: str,
        document_id: str,
        actor_id: str = Depends(require_owner),
    ) -> Response:
        download = container.document_management.download(
            case_id,
            document_id,
            actor_id,
        )
        encoded_name = quote(download.filename, safe="")
        return Response(
            content=download.content,
            media_type=download.mime_type,
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}",
            },
        )

    @router.get(
        "/processing-jobs/{job_id}",
        response_model=ProcessingJobOut,
        summary="查询文档处理任务",
    )
    def get_processing_job(
        job_id: str,
        actor_id: str = Depends(require_owner),
    ) -> ProcessingJobOut:
        job = container.document_management.get_job(job_id, actor_id)
        return _to_job_out(job)

    return router
