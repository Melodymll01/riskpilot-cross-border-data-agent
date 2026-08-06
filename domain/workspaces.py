"""RiskPilot V2 Workspace 领域模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from domain.models import BaseDomainModel

WorkspaceStatus = Literal["active", "archived"]
WorkspaceRole = Literal["viewer", "editor", "reviewer", "admin"]


class Workspace(BaseDomainModel):
    """V2 多租户工作空间。"""

    workspace_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=100)
    status: WorkspaceStatus = "active"
    created_by: str = Field(min_length=1)
    created_at: float
    updated_at: float

    @model_validator(mode="after")
    def validate_time_order(self) -> Workspace:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at 不能早于 created_at")
        return self


class WorkspaceMembership(BaseDomainModel):
    """用户在 Workspace 中的角色快照。"""

    workspace_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    role: WorkspaceRole
    joined_at: float
