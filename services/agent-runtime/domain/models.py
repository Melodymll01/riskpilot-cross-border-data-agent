"""Small domain models used by retrieval, parsing, and verified proposals."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Corpus = Literal["law", "user_docs"]
KbSourceType = Literal["file", "web"]


class BaseDomainModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_assignment=True,
        str_strip_whitespace=False,
    )


class ChatResponse(BaseDomainModel):
    content: str
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    token_usage: int = Field(default=0, ge=0)
    usage_available: bool = False

    @model_validator(mode="after")
    def validate_usage(self) -> ChatResponse:
        if self.token_usage < self.input_tokens + self.output_tokens:
            raise ValueError("token_usage cannot be smaller than input plus output tokens")
        return self


class Citation(BaseDomainModel):
    citation_id: str = ""
    source_id: str = ""
    source_type: str = Field(min_length=1)
    source_name: str = Field(min_length=1)
    title: str = ""
    source_url: str | None = None
    source_version: str | None = None
    text_snippet: str = ""
    chunk_id: str | None = None
    document_id: str | None = None
    document_version_id: str | None = None
    page_number: int | None = Field(default=None, ge=1)
    source_sha256: str | None = None
    clause_id: str | None = None


class Chunk(BaseDomainModel):
    chunk_id: str = Field(min_length=1)
    text: str
    source_type: str = Field(min_length=1)
    source_name: str = Field(min_length=1)
    title: str = ""
    source_url: str | None = None
    category: str = ""
    owner_id: str | None = None
    score: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class WebResult(BaseDomainModel):
    title: str
    url: str = Field(min_length=1)
    snippet: str = ""


class KbDocument(BaseDomainModel):
    source_name: str = Field(min_length=1)
    source_type: KbSourceType
    title: str = ""
    source_url: str | None = None
    chunk_count: int = Field(ge=0)
    category: str = ""
    owner_id: str | None = None


class KbChunk(BaseDomainModel):
    chunk_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    embedding_text: str | None = Field(default=None, min_length=1)
    source_name: str = Field(min_length=1)
    source_type: KbSourceType
    title: str = ""
    source_url: str | None = None
    chunk_index: int = Field(ge=0)
    category: str = ""
    owner_id: str | None = None
    source_metadata: dict[str, str] = Field(default_factory=dict)
