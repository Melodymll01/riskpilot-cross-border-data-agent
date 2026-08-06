"""V2 案件证据索引领域模型。"""

from __future__ import annotations

import re

from pydantic import Field, model_validator

from domain.models import BaseDomainModel

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class EvidenceChunk(BaseDomainModel):
    """可检索的案件证据块，作用域字段均为必填。"""

    chunk_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    document_version_id: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    chunk_index: int = Field(ge=0)
    text: str = Field(min_length=1)
    source_sha256: str = Field(min_length=64, max_length=64)
    created_at: float

    @model_validator(mode="after")
    def validate_chunk(self) -> EvidenceChunk:
        if not self.text.strip():
            raise ValueError("text 不能为空白字符串")
        if not _SHA256_RE.fullmatch(self.source_sha256):
            raise ValueError("source_sha256 必须是 64 位小写十六进制")
        return self


class EvidenceSearchHit(BaseDomainModel):
    """混合检索命中及其可解释分数。"""

    chunk: EvidenceChunk
    score: float = Field(ge=0.0)
    vector_score: float = Field(ge=-1.0, le=1.0)
    bm25_score: float = Field(ge=0.0)
