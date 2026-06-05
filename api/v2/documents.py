"""``/api/v2/documents/*`` 路由：知识库管理面（Step 016c）。

设计要点：
- 读端点（GET list / stats / detail）：``make_require_owner`` —— 任意登录用户可访问
- 写端点（POST / DELETE）：``make_require_admin`` —— 仅管理员可修改
- 业务编排走 ``container.kb_management``（Step 016b 装配的 use case）
- 文件上传：UUID 重命名落到 ``settings.upload_dir`` → use case → finally 删除临时文件
- 大小/后缀白名单沿用 v1 ``api/routes.py`` 语义；放在路由层做边界校验
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import anyio
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)

from api.v2.deps import make_require_admin, make_require_owner
from api.v2.schemas import (
    DeleteDocumentResponse,
    KbDocumentListResponse,
    KbDocumentOut,
    KbDocumentStatsResponse,
    KbIngestResponse,
    WebIngestRequest,
)

if TYPE_CHECKING:
    from app.container import AppContainer
    from app.use_cases.kb_management import KbIngestResult
    from domain.models import KbDocument

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS: frozenset[str] = frozenset({".pdf", ".txt", ".docx"})
"""允许上传的文件后缀（与 v1 ``api/routes.py`` 对齐）。"""


def _to_document_out(doc: KbDocument) -> KbDocumentOut:
    return KbDocumentOut(
        source_name=doc.source_name,
        source_type=doc.source_type,
        title=doc.title,
        source_url=doc.source_url,
        chunk_count=doc.chunk_count,
        category=doc.category,
    )


def _to_ingest_response(result: KbIngestResult) -> KbIngestResponse:
    return KbIngestResponse(
        success=result.success,
        source_name=result.source_name,
        chunk_count=result.chunk_count,
        message=result.message,
    )


def build_documents_routes(container: AppContainer) -> APIRouter:
    """构造 ``/documents`` 子 router；读端点 login-only，写端点 admin-only。"""

    router = APIRouter(prefix="/documents", tags=["documents"])
    require_owner = make_require_owner(container)
    require_admin = make_require_admin(container)
    upload_dir = Path(container.settings.upload_dir)
    max_upload_bytes = container.settings.max_upload_mb * 1024 * 1024

    @router.get(
        "",
        response_model=KbDocumentListResponse,
        summary="列出知识库所有文档（按 source_name 聚合）",
    )
    def list_documents(
        _owner_id: str = Depends(require_owner),
    ) -> KbDocumentListResponse:
        docs = container.kb_management.list_documents()
        total = sum(d.chunk_count for d in docs)
        return KbDocumentListResponse(
            documents=[_to_document_out(d) for d in docs],
            total_chunks=total,
        )

    @router.get(
        "/stats",
        response_model=KbDocumentStatsResponse,
        summary="知识库总览统计（文档数 + chunk 数）",
    )
    def get_stats(
        _owner_id: str = Depends(require_owner),
    ) -> KbDocumentStatsResponse:
        # 调一次 list 即可拿到 doc_count；chunk_count 走 use case
        docs = container.kb_management.list_documents()
        chunks = container.kb_management.count_chunks()
        return KbDocumentStatsResponse(
            document_count=len(docs),
            chunk_count=chunks,
        )

    @router.get(
        "/{source_name}",
        response_model=KbDocumentOut,
        summary="按 source_name 取文档详情",
    )
    def get_document(
        source_name: str,
        _owner_id: str = Depends(require_owner),
    ) -> KbDocumentOut:
        doc = container.kb_management.get_document(source_name)
        if doc is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error_code": "DOCUMENT_NOT_FOUND",
                    "message": f"document {source_name!r} not found",
                },
            )
        return _to_document_out(doc)

    @router.delete(
        "/{source_name}",
        response_model=DeleteDocumentResponse,
        summary="按 source_name 删除文档（连带所有 chunks）",
    )
    def delete_document(
        source_name: str,
        _admin_id: str = Depends(require_admin),
    ) -> DeleteDocumentResponse:
        deleted = container.kb_management.delete_document(source_name)
        if deleted == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error_code": "DOCUMENT_NOT_FOUND",
                    "message": f"document {source_name!r} not found",
                },
            )
        return DeleteDocumentResponse(
            ok=True,
            source_name=source_name,
            deleted_count=deleted,
        )

    @router.post(
        "/file",
        response_model=KbIngestResponse,
        status_code=status.HTTP_201_CREATED,
        summary="上传文件入库（multipart/form-data）",
    )
    async def ingest_file(
        file: UploadFile = File(..., description="PDF / TXT / DOCX，最大 ${max_upload_mb}MB"),
        category: str = Query("", description="文档分类标签", max_length=100),
        _admin_id: str = Depends(require_admin),
    ) -> KbIngestResponse:
        original_name = file.filename or "unknown"
        suffix = Path(original_name).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error_code": "UNSUPPORTED_FILE_TYPE",
                    "message": (
                        f"不支持的文件格式: {suffix or '<empty>'}，"
                        f"仅支持 {sorted(ALLOWED_EXTENSIONS)}"
                    ),
                },
            )

        content = await file.read()
        if len(content) > max_upload_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail={
                    "error_code": "FILE_TOO_LARGE",
                    "message": (
                        f"文件过大，最大支持 {container.settings.max_upload_mb}MB"
                    ),
                },
            )

        # UUID 文件名彻底消除路径穿越；后缀保留用于解析器识别格式
        safe_name = f"{uuid4().hex}{suffix}"
        save_path = upload_dir / safe_name
        try:
            upload_dir.mkdir(parents=True, exist_ok=True)
            save_path.write_bytes(content)
            logger.info(
                "uploaded file saved tmp=%s original=%s size=%d category=%s",
                save_path,
                original_name,
                len(content),
                category or "<none>",
            )
            result = await anyio.to_thread.run_sync(
                lambda: container.kb_management.ingest_file(
                    str(save_path),
                    original_filename=original_name,
                    category=category or None,
                )
            )
            return _to_ingest_response(result)
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error_code": "INGEST_FAILED", "message": str(e)},
            ) from e
        finally:
            if save_path.exists():
                try:
                    os.remove(save_path)
                except OSError:
                    logger.warning("临时文件清理失败 path=%s", save_path)

    @router.post(
        "/web",
        response_model=KbIngestResponse,
        status_code=status.HTTP_201_CREATED,
        summary="采集网页入库",
    )
    async def ingest_web(
        body: WebIngestRequest,
        _admin_id: str = Depends(require_admin),
    ) -> KbIngestResponse:
        try:
            result = await anyio.to_thread.run_sync(
                lambda: container.kb_management.ingest_web(
                    body.url,
                    category=body.category or None,
                )
            )
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error_code": "INGEST_FAILED", "message": str(e)},
            ) from e
        return _to_ingest_response(result)

    return router
