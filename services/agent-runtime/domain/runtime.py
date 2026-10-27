"""Frozen internal-v1 contracts and runtime-only value objects."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION: Literal["1.0"] = "1.0"


class StrictContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ServicePrincipal(StrictContractModel):
    iss: Literal["riskpilot-go"]
    aud: Literal["riskpilot-agent"]
    sub: str
    iat: int
    exp: int
    jti: str = Field(min_length=16, max_length=128)
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
    target: str = Field(pattern=r"^/")

    @model_validator(mode="after")
    def validate_lifetime(self) -> ServicePrincipal:
        if not 0 < self.exp - self.iat <= 30:
            raise ValueError("service principal lifetime must be between 1 and 30 seconds")
        return self


class AttachmentVersion(StrictContractModel):
    document_id: str = Field(min_length=1, max_length=100)
    version_id: str = Field(min_length=1, max_length=100)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class MemoryReference(StrictContractModel):
    memory_id: str = Field(min_length=1, max_length=100)
    revision: int = Field(ge=1)
    content: str = Field(min_length=1, max_length=800)
    source_message_id: str = Field(min_length=1, max_length=100)
    source_quote: str = Field(min_length=1, max_length=8000)


class ContextMessage(StrictContractModel):
    message_id: str = Field(min_length=1, max_length=100)
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=20000)


class RunBudget(StrictContractModel):
    deadline_at: float
    max_model_calls: int = Field(ge=1, le=50)
    max_tool_calls: int = Field(ge=1, le=50)


class RunExecute(StrictContractModel):
    schema_version: Literal["1.0"]
    run_id: str = Field(min_length=1, max_length=100)
    attempt_id: int = Field(ge=1)
    thread_id: str = Field(min_length=1, max_length=100)
    owner_id: str = Field(min_length=1, max_length=200)
    task_id: str = Field(min_length=1, max_length=100)
    input_message_id: str = Field(min_length=1, max_length=100)
    question: str = Field(min_length=1, max_length=8000)
    attachment_versions: list[AttachmentVersion] = Field(max_length=20)
    memory_snapshot: list[MemoryReference] = Field(max_length=100)
    recent_messages: list[ContextMessage] = Field(default_factory=list, max_length=50)
    conversation_summary: str = Field(default="", max_length=20000)
    resume: bool = False
    budget: RunBudget

    @model_validator(mode="after")
    def validate_snapshot_identity(self) -> RunExecute:
        if len({item.document_id for item in self.attachment_versions}) != len(
            self.attachment_versions
        ):
            raise ValueError("attachment document_id values must be unique")
        if len({item.memory_id for item in self.memory_snapshot}) != len(self.memory_snapshot):
            raise ValueError("memory_id values must be unique")
        if len({item.message_id for item in self.recent_messages}) != len(self.recent_messages):
            raise ValueError("recent message_id values must be unique")
        return self


class RunControlCommand(StrictContractModel):
    schema_version: Literal["1.0"]
    run_id: str = Field(min_length=1, max_length=100)
    attempt_id: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=200)


class DocumentProcess(StrictContractModel):
    schema_version: Literal["1.0"]
    operation: Literal["ingest", "delete"]
    document_id: str = Field(min_length=1, max_length=100)
    version_id: str = Field(min_length=1, max_length=100)
    actor_id: str = Field(min_length=1, max_length=200)
    owner_id: str | None = Field(min_length=1, max_length=200)
    source_name: str = Field(min_length=1, max_length=1000)
    source_type: Literal["file", "web"]
    source_url: str | None = Field(max_length=2000)
    category: str = Field(max_length=100)
    mime_type: str = Field(min_length=1, max_length=255)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_base64: str | None

    @model_validator(mode="after")
    def validate_operation_payload(self) -> DocumentProcess:
        if self.operation == "delete" and self.content_base64 is not None:
            raise ValueError("delete must not contain content_base64")
        if self.operation == "ingest" and self.source_type == "file":
            if not self.content_base64 or self.source_url is not None:
                raise ValueError("file ingest requires content_base64 and forbids source_url")
            try:
                base64.b64decode(self.content_base64, validate=True)
            except ValueError as exc:
                raise ValueError("content_base64 must be valid base64") from exc
        if self.operation == "ingest" and self.source_type == "web":
            if self.content_base64 is not None or not self.source_url:
                raise ValueError("web ingest requires source_url and forbids content_base64")
            if not self.source_url.startswith(("http://", "https://")):
                raise ValueError("web source_url must use http or https")
        return self


class DocumentResult(StrictContractModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    document_id: str = Field(min_length=1, max_length=100)
    status: Literal["ready", "deleted", "failed"]
    title: str = Field(max_length=1000)
    chunk_count: int = Field(ge=0)
    error_code: str | None
    message: str


RunEventType = Literal[
    "stage",
    "tool_call",
    "tool_result",
    "tool_error",
    "draft_delta",
    "draft_discarded",
    "usage",
    "interrupted",
    "result_proposal",
]


class StagePayload(StrictContractModel):
    stage: Literal["agent", "tool", "generation", "verification"]


class ToolCallPayload(StrictContractModel):
    tool_name: str = Field(min_length=1)
    tool_args: dict[str, Any]


class ToolResultPayload(StrictContractModel):
    tool_name: str = Field(min_length=1)
    result: Any


class ToolErrorPayload(StrictContractModel):
    tool_name: str = Field(min_length=1)
    error: str = Field(min_length=1)


class DraftDeltaPayload(StrictContractModel):
    text: str = Field(min_length=1)


class DraftDiscardedPayload(StrictContractModel):
    reason: str = Field(min_length=1)


class UsagePayload(StrictContractModel):
    model_calls: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class InterruptedPayload(StrictContractModel):
    stage: Literal["agent", "tool", "generation", "verification"]


class ResultProposal(StrictContractModel):
    status: Literal["completed", "failed"]
    answer: str = Field(min_length=1)
    citations: list[dict[str, Any]]
    verified_citation_ids: list[str]
    verified_context_message_ids: list[str]
    clarification_fields: list[str] = Field(default_factory=list)
    error_type: str | None = None
    end_reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> ResultProposal:
        if len(set(self.verified_citation_ids)) != len(self.verified_citation_ids):
            raise ValueError("verified_citation_ids must be unique")
        if len(set(self.verified_context_message_ids)) != len(self.verified_context_message_ids):
            raise ValueError("verified_context_message_ids must be unique")
        return self


_PAYLOAD_MODELS: dict[str, type[StrictContractModel]] = {
    "stage": StagePayload,
    "tool_call": ToolCallPayload,
    "tool_result": ToolResultPayload,
    "tool_error": ToolErrorPayload,
    "draft_delta": DraftDeltaPayload,
    "draft_discarded": DraftDiscardedPayload,
    "usage": UsagePayload,
    "interrupted": InterruptedPayload,
    "result_proposal": ResultProposal,
}


class RunEvent(StrictContractModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run_id: str = Field(min_length=1, max_length=100)
    attempt_id: int = Field(ge=1)
    event_seq: int = Field(ge=1)
    event_type: RunEventType
    payload: dict[str, Any]

    @model_validator(mode="after")
    def validate_payload(self) -> RunEvent:
        _PAYLOAD_MODELS[self.event_type].model_validate(self.payload)
        return self


@dataclass(frozen=True)
class RuntimeSignal:
    event_type: RunEventType
    payload: dict[str, Any]


def snapshot_fingerprint(snapshot: RunExecute) -> str:
    return hashlib.sha256(snapshot.model_dump_json().encode()).hexdigest()


def checkpoint_context_fingerprint(snapshot: RunExecute) -> str:
    """Fingerprint fields that must stay immutable across resume attempts."""

    payload = snapshot.model_dump(exclude={"attempt_id", "resume", "budget"})
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
