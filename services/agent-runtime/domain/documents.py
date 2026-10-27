"""Immutable document-version input supplied by the Go control plane."""

from __future__ import annotations

import re

from pydantic import Field, model_validator

from domain.models import BaseDomainModel

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class DocumentVersion(BaseDomainModel):
    document_id: str = Field(min_length=1, max_length=100)
    version_id: str = Field(min_length=1, max_length=100)
    sha256: str = Field(min_length=64, max_length=64)
    mime_type: str = Field(min_length=1, max_length=255)

    @model_validator(mode="after")
    def validate_version(self) -> DocumentVersion:
        if not _SHA256_RE.fullmatch(self.sha256):
            raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
        return self
