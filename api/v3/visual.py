"""V3 Case 图片证据上传、文本搜图与原图下载。"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import quote

import anyio
from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from fastapi.responses import Response

from api.v2.deps import make_require_owner
from api.v3.schemas import (
    VisualAssetOut,
    VisualSearchHitOut,
    VisualSearchResponse,
)

if TYPE_CHECKING:
    from app.container import AppContainer
    from domain.visual import VisualAsset


def _to_asset_out(asset: VisualAsset) -> VisualAssetOut:
    return VisualAssetOut(
        asset_id=asset.asset_id,
        workspace_id=asset.workspace_id,
        case_id=asset.case_id,
        filename=asset.filename,
        mime_type=asset.mime_type,
        width=asset.width,
        height=asset.height,
        caption=asset.caption,
        created_by=asset.created_by,
        created_at=asset.created_at,
    )


def build_visual_routes(container: AppContainer) -> APIRouter:
    router = APIRouter(prefix="/cases", tags=["v3-visual-evidence"])
    require_owner = make_require_owner(container)
    max_bytes = container.settings.visual_max_upload_mb * 1024 * 1024

    @router.post(
        "/{case_id}/visual-assets",
        response_model=VisualAssetOut,
        status_code=status.HTTP_201_CREATED,
        summary="上传 Case 图片并建立 Chinese-CLIP 向量",
    )
    async def upload_visual_asset(
        case_id: str,
        file: UploadFile = File(...),
        caption: str = Form(default="", max_length=1000),
        actor_id: str = Depends(require_owner),
    ) -> VisualAssetOut:
        content = await file.read(max_bytes + 1)
        asset = await anyio.to_thread.run_sync(
            lambda: container.visual_evidence.upload(
                actor_id,
                case_id=case_id,
                filename=file.filename or "",
                content=content,
                caption=caption,
            )
        )
        return _to_asset_out(asset)

    @router.get(
        "/{case_id}/visual-assets/search",
        response_model=VisualSearchResponse,
        summary="使用自然语言在当前 Case 中检索图片",
    )
    def search_visual_assets(
        case_id: str,
        query: str = Query(min_length=1, max_length=1000),
        top_k: int = Query(default=5, ge=1, le=20),
        actor_id: str = Depends(require_owner),
    ) -> VisualSearchResponse:
        hits = container.visual_evidence.search(
            actor_id,
            case_id=case_id,
            query=query,
            top_k=top_k,
        )
        return VisualSearchResponse(
            hits=[
                VisualSearchHitOut(asset=_to_asset_out(hit.asset), score=hit.score)
                for hit in hits
            ]
        )

    @router.get(
        "/{case_id}/visual-assets/{asset_id}/content",
        summary="下载当前 Case 的图片原件",
    )
    def download_visual_asset(
        case_id: str,
        asset_id: str,
        actor_id: str = Depends(require_owner),
    ) -> Response:
        asset, content = container.visual_evidence.download(
            actor_id,
            case_id=case_id,
            asset_id=asset_id,
        )
        return Response(
            content=content,
            media_type=asset.mime_type,
            headers={
                "Content-Disposition": (
                    f"inline; filename*=UTF-8''{quote(asset.filename, safe='')}"
                )
            },
        )

    return router
