"""Owner-scoped attachments, parse/index status and authorized source downloads."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response

from api.v2.deps import make_require_owner

if TYPE_CHECKING:
    from app.container import AppContainer


def build_file_routes(container: AppContainer) -> APIRouter:
    router = APIRouter(prefix="/files", tags=["attachments"])
    owner = make_require_owner(container)

    @router.post("", status_code=201)
    def upload(file: UploadFile = File(...), owner_id: str = Depends(owner)) -> dict:
        content = file.file.read(container.settings.max_upload_mb * 1024 * 1024 + 1)
        result = container.document_management.upload(
            owner_id, filename=file.filename or "", content=content
        )
        return {
            "document": result.document.model_dump(),
            "version": result.version.model_dump(exclude={"object_key"}),
            "job": result.job.model_dump(),
        }

    @router.get("")
    def list_files(owner_id: str = Depends(owner)) -> dict:
        return {
            "documents": [
                {
                    "document": item.document.model_dump(),
                    "job": item.latest_job.model_dump() if item.latest_job else None,
                }
                for item in container.document_management.list_documents(owner_id)
            ]
        }

    @router.get("/search")
    def search(query: str, owner_id: str = Depends(owner)) -> dict:
        return {
            "hits": [
                hit.model_dump() for hit in container.evidence_search.search(owner_id, query=query)
            ]
        }

    @router.get("/jobs/{job_id}")
    def get_job(job_id: str, owner_id: str = Depends(owner)) -> dict:
        return container.document_management.get_job(job_id, owner_id).model_dump()

    @router.get("/{document_id}")
    def detail(document_id: str, owner_id: str = Depends(owner)) -> dict:
        result = container.document_management.get_detail(document_id, owner_id)
        return {
            "document": result.document.model_dump(),
            "job": result.latest_job.model_dump() if result.latest_job else None,
        }

    @router.get("/{document_id}/download")
    def download(document_id: str, owner_id: str = Depends(owner)) -> Response:
        result = container.document_management.download(document_id, owner_id)
        return Response(
            result.content,
            media_type=result.mime_type,
            headers={
                "Content-Disposition": "attachment; filename*=UTF-8''" + quote(result.filename)
            },
        )

    @router.post("/jobs/{job_id}/retry")
    def retry(job_id: str, owner_id: str = Depends(owner)) -> dict:
        job = container.document_management.get_job(job_id, owner_id)
        if job.retry_count >= 3:
            raise HTTPException(409, "已达到重试上限")
        return container.document_management.retry_job(job_id, owner_id).model_dump()

    @router.post("/jobs/{job_id}/cancel")
    def cancel(job_id: str, owner_id: str = Depends(owner)) -> dict:
        return container.document_management.cancel_job(job_id, owner_id).model_dump()

    return router
