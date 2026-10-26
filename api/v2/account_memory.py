"""Account memory CRUD; identity comes exclusively from the authenticated cookie."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from api.v2.deps import make_require_owner
from domain.account_memory import AccountMemoryConflict, MemoryInputKind, ProfileField


class MemoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=800)
    kind: MemoryInputKind = "auto"
    profile_field: ProfileField | None = None
    expires_at: float | None = None
    source_task_id: str | None = None
    source_message_id: str | None = None


class MemoryEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=800)
    expected_revision: int = Field(ge=1)
    expires_at: float | None = None


class MemorySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class MemoryConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)


def build_account_memory_routes(container: Any) -> APIRouter:
    router = APIRouter(prefix="/memory", tags=["account-memory"])
    service = container.account_memory
    identify = make_require_owner(container)

    def owner(response: Response, owner_id: str = Depends(identify)) -> str:
        response.headers["Cache-Control"] = "no-store"
        try:
            service.require_account(owner_id)
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        return owner_id

    @router.get("")
    def list_memory(owner_id: str = Depends(owner)) -> dict:
        return {
            "enabled": service.enabled(owner_id),
            "memories": [service.present(m) for m in service.list_memories(owner_id)],
            "policy": {
                "business_ttl_days": service.policy.business_ttl_days,
                "profile_ttl_days": service.policy.profile_ttl_days,
                "profile_review_days": service.policy.profile_review_days,
            },
        }

    @router.put("/settings")
    def settings(body: MemorySettings, owner_id: str = Depends(owner)) -> dict:
        service.set_enabled(owner_id, body.enabled)
        return {"enabled": body.enabled}

    @router.post("", status_code=201)
    def create(body: MemoryCreate, owner_id: str = Depends(owner)) -> dict:
        before = {item.memory_id: item.revision for item in service.list_memories(owner_id)}
        memories = service.save_declaration(owner_id, **body.model_dump())
        return {
            **service.present(memories[0]),
            "saved_count": len(memories),
            "related_memories": [service.present(item) for item in memories[1:]],
            "unchanged": all(before.get(item.memory_id) == item.revision for item in memories),
        }

    @router.patch("/{memory_id}")
    def edit(memory_id: str, body: MemoryEdit, owner_id: str = Depends(owner)) -> dict:
        try:
            return service.present(
                service.edit(owner_id, memory_id, **body.model_dump(exclude_unset=True))
            )
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except AccountMemoryConflict as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/{memory_id}/confirm")
    def confirm(memory_id: str, body: MemoryConfirm, owner_id: str = Depends(owner)) -> dict:
        try:
            return service.present(
                service.confirm(owner_id, memory_id, expected_revision=body.expected_revision)
            )
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except AccountMemoryConflict as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.delete("/{memory_id}")
    def delete(memory_id: str, owner_id: str = Depends(owner)) -> dict:
        if not service.delete(owner_id, memory_id):
            raise HTTPException(404, "记忆不存在")
        return {"deleted": True}

    @router.delete("")
    def clear(owner_id: str = Depends(owner)) -> dict:
        return {"deleted_count": service.clear(owner_id)}

    return router
