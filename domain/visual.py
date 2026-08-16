"""案件图片证据与跨模态检索模型。"""

from __future__ import annotations

import re

from pydantic import Field

from domain.models import BaseDomainModel

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class VisualAsset(BaseDomainModel):
    """Case 作用域内的单张图片证据。"""

    asset_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    object_key: str = Field(min_length=1)
    filename: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(pattern=r"^image/(png|jpeg|webp)$")
    sha256: str = Field(min_length=64, max_length=64)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    caption: str = Field(default="", max_length=1000)
    created_by: str = Field(min_length=1)
    created_at: float

    def model_post_init(self, __context: object) -> None:
        if not _SHA256_RE.fullmatch(self.sha256):
            raise ValueError("sha256 必须是 64 位小写十六进制")


class VisualSearchHit(BaseDomainModel):
    """文本查询命中的图片及余弦相似度。"""

    asset: VisualAsset
    score: float = Field(ge=-1.0, le=1.0)
