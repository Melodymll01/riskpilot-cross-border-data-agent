"""Case 图片证据上传与文本搜图用例。"""

from __future__ import annotations

import hashlib
import io
import time
import uuid
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from PIL import Image, UnidentifiedImageError

from domain.visual import VisualAsset, VisualSearchHit

if TYPE_CHECKING:
    from app.use_cases.case_management import CaseManagementUseCase
    from app.use_cases.workspace_management import WorkspaceManagementUseCase
    from domain.ports import ObjectStorePort, VisualEmbedPort, VisualIndexPort

_MIME_BY_FORMAT = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
}
_SUFFIX_BY_MIME = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}
_WRITE_ROLES = {"editor", "reviewer", "admin"}


class VisualEvidenceUseCase:
    def __init__(
        self,
        *,
        visual_index: VisualIndexPort,
        embedder: VisualEmbedPort,
        object_store: ObjectStorePort,
        case_management: CaseManagementUseCase,
        workspace_management: WorkspaceManagementUseCase,
        max_upload_bytes: int,
    ) -> None:
        self._index = visual_index
        self._embedder = embedder
        self._objects = object_store
        self._cases = case_management
        self._workspaces = workspace_management
        self._max_upload_bytes = max_upload_bytes

    def upload(
        self,
        actor_id: str,
        *,
        case_id: str,
        filename: str,
        content: bytes,
        caption: str = "",
    ) -> VisualAsset:
        case = self._cases.get_case(case_id, actor_id)
        self._workspaces.require_role(
            case.workspace_id,
            actor_id,
            _WRITE_ROLES,
            action="上传案件图片证据",
        )
        if not content or len(content) > self._max_upload_bytes:
            raise ValueError("图片为空或超过大小限制")
        mime_type, width, height = _inspect_image(content)
        asset_id = f"visual_{uuid.uuid4().hex[:16]}"
        suffix = _SUFFIX_BY_MIME[mime_type]
        object_key = str(
            PurePosixPath(
                case.workspace_id,
                case.case_id,
                "visual",
                f"{asset_id}{suffix}",
            )
        )
        asset = VisualAsset(
            asset_id=asset_id,
            workspace_id=case.workspace_id,
            case_id=case.case_id,
            object_key=object_key,
            filename=_safe_filename(filename, suffix),
            mime_type=mime_type,
            sha256=hashlib.sha256(content).hexdigest(),
            width=width,
            height=height,
            caption=caption.strip(),
            created_by=actor_id,
            created_at=time.time(),
        )
        embedding = self._embedder.embed_images([content])[0]
        self._objects.put(object_key, content)
        try:
            self._index.add(asset, embedding)
        except Exception:
            self._objects.delete(object_key)
            raise
        return asset

    def search(
        self,
        actor_id: str,
        *,
        case_id: str,
        query: str,
        top_k: int = 5,
    ) -> list[VisualSearchHit]:
        case = self._cases.get_case(case_id, actor_id)
        if not query.strip():
            raise ValueError("query 不能为空")
        embedding = self._embedder.embed_texts([query.strip()])[0]
        return self._index.search(
            workspace_id=case.workspace_id,
            case_id=case.case_id,
            query_embedding=embedding,
            top_k=top_k,
        )

    def download(
        self,
        actor_id: str,
        *,
        case_id: str,
        asset_id: str,
    ) -> tuple[VisualAsset, bytes]:
        case = self._cases.get_case(case_id, actor_id)
        asset = self._index.get(asset_id)
        if (
            asset is None
            or asset.case_id != case.case_id
            or asset.workspace_id != case.workspace_id
        ):
            raise ValueError("图片不存在")
        return asset, self._objects.read(asset.object_key)


def _inspect_image(content: bytes) -> tuple[str, int, int]:
    try:
        with Image.open(io.BytesIO(content)) as image:
            image.verify()
        with Image.open(io.BytesIO(content)) as image:
            mime_type = _MIME_BY_FORMAT.get(str(image.format).upper())
            if mime_type is None:
                raise ValueError("仅支持 PNG/JPEG/WebP")
            width, height = image.size
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("图片无法解码") from exc
    if width < 16 or height < 16 or width * height > 25_000_000:
        raise ValueError("图片尺寸不在允许范围")
    return mime_type, width, height


def _safe_filename(filename: str, suffix: str) -> str:
    name = PurePosixPath((filename or "").replace("\\", "/")).name.strip()
    if not name:
        return f"image{suffix}"
    stem = PurePosixPath(name).stem[:200] or "image"
    return f"{stem}{suffix}"
