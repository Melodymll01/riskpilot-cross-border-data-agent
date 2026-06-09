"""``/api/v2/documents/*`` 路由：知识库管理面（Step 016c）。

设计要点：
- 读端点（GET list / stats / detail）：``make_require_owner`` —— 任意登录用户可访问
- 写端点（POST / DELETE）：Step 025a 起 ``make_require_owner`` —— 普通用户可上传/删自己；
  admin 仍享 admin 路径（写公共 / 删任意）。具体策略在路由内根据 ``is_admin`` 分支。
- 业务编排走 ``container.kb_management``（Step 016b 装配的 use case）
- 文件上传：UUID 重命名落到 ``settings.upload_dir`` → use case → finally 删除临时文件
- 大小/后缀白名单沿用 v1 ``api/routes.py`` 语义；放在路由层做边界校验

Step 025a 关键变化：
- GET list/stats/detail 加 ``?scope=public|mine|all`` 查询参数；默认 ``all`` 表示
  公共 ∪ 自己（admin 视角等价全库）
- POST file/web 加 ``?as_public=true`` admin-only 开关；不传时普通用户上传到自己私人，
  admin 默认上传到公共
- DELETE 不再强制 admin：普通用户可删自己的；admin 可删任意
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Literal
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

from api.v2.deps import make_require_owner
from api.v2.schemas import (
    DeleteDocumentResponse,
    KbDocumentListResponse,
    KbDocumentOut,
    KbDocumentStatsResponse,
    KbIngestResponse,
    WebIngestRequest,
)

if TYPE_CHECKING:
    from api.v2.ratelimit import RateLimiter
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
        owner_id=doc.owner_id,
    )


def _to_ingest_response(result: KbIngestResult) -> KbIngestResponse:
    return KbIngestResponse(
        success=result.success,
        source_name=result.source_name,
        chunk_count=result.chunk_count,
        message=result.message,
    )


def build_documents_routes(
    container: AppContainer, *, limiter: RateLimiter | None = None
) -> APIRouter:
    """构造 ``/documents`` 子 router；读 / 写均 require_owner，admin 在内部判别。"""

    router = APIRouter(prefix="/documents", tags=["documents"])
    require_owner = make_require_owner(container)
    ingest_deps = (
        [Depends(limiter.dependency(container.settings.rate_limit_ingest))]
        if limiter is not None
        else []
    )
    upload_dir = Path(container.settings.upload_dir)
    max_upload_bytes = container.settings.max_upload_mb * 1024 * 1024
    admin_set = set(container.settings.admin_user_ids)

    def _is_admin(uid: str) -> bool:
        return uid in admin_set

    @router.get(
        "",
        response_model=KbDocumentListResponse,
        summary="列出知识库文档（按 source_name 聚合；scope 决定可见范围）",
    )
    def list_documents(
        scope: Literal["public", "mine", "all"] = Query(
            "all",
            description="可见范围：public=仅公共；mine=仅自己；all=两者合集（admin 视角=全库）",
        ),
        owner_id: str = Depends(require_owner),
    ) -> KbDocumentListResponse:
        docs = container.kb_management.list_documents(
            viewer_id=owner_id,
            viewer_is_admin=_is_admin(owner_id),
            scope=scope,
        )
        total = sum(d.chunk_count for d in docs)
        return KbDocumentListResponse(
            documents=[_to_document_out(d) for d in docs],
            total_chunks=total,
        )

    @router.get(
        "/stats",
        response_model=KbDocumentStatsResponse,
        summary="知识库总览统计（document_count 按 scope，chunk_count 为全库总数）",
    )
    def get_stats(
        scope: Literal["public", "mine", "all"] = Query(
            "all",
            description="文档数按 scope 计算；chunk 总数仍是全库（仅 admin 视角准确）",
        ),
        owner_id: str = Depends(require_owner),
    ) -> KbDocumentStatsResponse:
        # 调一次 list 即可拿到 doc_count；chunk_count 走 use case
        docs = container.kb_management.list_documents(
            viewer_id=owner_id,
            viewer_is_admin=_is_admin(owner_id),
            scope=scope,
        )
        chunks = container.kb_management.count_chunks()
        return KbDocumentStatsResponse(
            document_count=len(docs),
            chunk_count=chunks,
        )

    @router.get(
        "/{source_name}",
        response_model=KbDocumentOut,
        summary="按 source_name 取文档详情（仅可见范围内）",
    )
    def get_document(
        source_name: str,
        scope: Literal["public", "mine", "all"] = Query("all"),
        owner_id: str = Depends(require_owner),
    ) -> KbDocumentOut:
        doc = container.kb_management.get_document(
            source_name,
            viewer_id=owner_id,
            viewer_is_admin=_is_admin(owner_id),
            scope=scope,
        )
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
        summary="删除文档（admin 可删任意；普通用户仅可删自己上传的）",
    )
    def delete_document(
        source_name: str,
        owner_id: str = Depends(require_owner),
    ) -> DeleteDocumentResponse:
        is_admin = _is_admin(owner_id)
        deleted = container.kb_management.delete_document(
            source_name,
            actor_id=owner_id,
            actor_is_admin=is_admin,
        )
        if deleted == 0:
            # 普通用户视角下也走 404；不暴露「文档存在但属于他人」信息
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error_code": "DOCUMENT_NOT_FOUND",
                    "message": f"document {source_name!r} not found or not yours",
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
        dependencies=ingest_deps,
        summary="上传文件入库（multipart/form-data）。普通用户入私人；admin 默认入公共",
    )
    async def ingest_file(
        file: UploadFile = File(..., description="PDF / TXT / DOCX，最大 ${max_upload_mb}MB"),
        category: str = Query("", description="文档分类标签", max_length=100),
        as_public: bool = Query(
            False,
            description="admin-only：true 时入公共库（owner_id=None）。普通用户忽略该参数。",
        ),
        owner_id: str = Depends(require_owner),
    ) -> KbIngestResponse:
        is_admin = _is_admin(owner_id)
        # 策略：admin 默认 as_public=True，可显式置 False 入私人；普通用户必入自己私人
        if is_admin:
            target_owner: str | None = None if as_public else owner_id
        else:
            target_owner = owner_id  # 普通用户强制入私人

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
                "uploaded file saved tmp=%s original=%s size=%d category=%s owner=%s",
                save_path,
                original_name,
                len(content),
                category or "<none>",
                target_owner or "<public>",
            )
            result = await anyio.to_thread.run_sync(
                lambda: container.kb_management.ingest_file(
                    str(save_path),
                    original_filename=original_name,
                    category=category or None,
                    owner_id=target_owner,
                    actor_id=owner_id,
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
        dependencies=ingest_deps,
        summary="采集网页入库。普通用户入私人；admin 默认入公共",
    )
    async def ingest_web(
        body: WebIngestRequest,
        as_public: bool = Query(
            False,
            description="admin-only：true 时入公共库。普通用户忽略该参数。",
        ),
        owner_id: str = Depends(require_owner),
    ) -> KbIngestResponse:
        is_admin = _is_admin(owner_id)
        if is_admin:
            target_owner: str | None = None if as_public else owner_id
        else:
            target_owner = owner_id
        try:
            result = await anyio.to_thread.run_sync(
                lambda: container.kb_management.ingest_web(
                    body.url,
                    category=body.category or None,
                    owner_id=target_owner,
                    actor_id=owner_id,
                )
            )
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error_code": "INGEST_FAILED", "message": str(e)},
            ) from e
        return _to_ingest_response(result)

    return router
