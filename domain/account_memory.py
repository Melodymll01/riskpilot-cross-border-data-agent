"""User-managed, attributable account memory; separate from chat history."""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import Field, FiniteFloat, model_validator

from domain.models import BaseDomainModel


class AccountMemoryConflict(ValueError):
    """The record changed since it was read."""


MemoryKind = Literal["profile", "preference", "business_context"]
MemoryInputKind = Literal["auto", "profile", "preference", "business_context", "fact"]
ProfileField = Literal["name", "role", "organization"]


class AccountMemory(BaseDomainModel):
    memory_id: str
    owner_id: str
    kind: MemoryKind
    content: str = Field(min_length=1, max_length=800)
    profile_field: ProfileField | None = None
    value: str | None = Field(default=None, min_length=1, max_length=200)
    source_type: Literal["manual", "user_message"]
    source_task_id: str | None = None
    source_message_id: str | None = None
    source_quote: str = ""
    revision: int = Field(default=1, ge=1)
    created_at: float
    updated_at: float
    expires_at: FiniteFloat | None = Field(default=None, gt=0)
    review_at: FiniteFloat | None = Field(default=None, gt=0)
    retention_days: FiniteFloat | None = Field(default=None, gt=0)
    schema_version: int = Field(default=2, ge=1, le=2)
    profile_parser_version: int = Field(default=1, ge=0, le=1)

    @model_validator(mode="before")
    @classmethod
    def read_legacy_kind(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("kind") == "fact":
            return {
                **data,
                "kind": "business_context",
                "schema_version": data.get("schema_version", 1),
            }
        return data

    @model_validator(mode="after")
    def validate_profile(self) -> AccountMemory:
        if self.kind == "profile":
            if self.profile_field is None or not self.value:
                raise ValueError("用户档案必须指定字段和值")
            if self.value not in self.content or self.value not in self.source_quote:
                raise ValueError("档案值必须逐字存在于来源原话中")
        elif self.profile_field is not None or self.value is not None:
            raise ValueError("只有用户档案可以设置档案字段和值")
        if (
            self.review_at is not None
            and self.expires_at is not None
            and self.review_at > self.expires_at
        ):
            raise ValueError("复核时间不能晚于过期时间")
        return self


class AccountMemoryRepoPort(Protocol):
    def list_memories(self, owner_id: str) -> list[AccountMemory]: ...
    def get(self, owner_id: str, memory_id: str) -> AccountMemory | None: ...
    def save(
        self, memory: AccountMemory, *, expected_revision: int | None = None
    ) -> AccountMemory: ...
    def save_many(self, memories: list[AccountMemory]) -> list[AccountMemory]: ...
    def delete(
        self, owner_id: str, memory_id: str, *, expected_revision: int | None = None
    ) -> bool: ...
    def clear(self, owner_id: str) -> int: ...
    def enabled(self, owner_id: str) -> bool: ...
    def set_enabled(self, owner_id: str, enabled: bool) -> None: ...
    def snapshot(self, run_id: str, owner_id: str, memories: list[AccountMemory]) -> None: ...
    def references(self, run_id: str, owner_id: str) -> dict[str, int]: ...
