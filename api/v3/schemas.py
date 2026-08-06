"""RiskPilot V3 HTTP 请求与响应模型。"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

WorkspaceRoleValue = Literal["viewer", "editor", "reviewer", "admin"]
WorkspaceStatusValue = Literal["active", "archived"]
CaseStatusValue = Literal[
    "draft",
    "collecting",
    "processing_documents",
    "facts_pending_confirmation",
    "ready_for_assessment",
    "assessing",
    "review_required",
    "completed",
    "archived",
]


class CreateWorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)


class WorkspaceOut(BaseModel):
    workspace_id: str
    name: str
    status: WorkspaceStatusValue
    created_by: str
    created_at: float
    updated_at: float


class WorkspaceListResponse(BaseModel):
    workspaces: list[WorkspaceOut]


class UpsertWorkspaceMemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: WorkspaceRoleValue


class WorkspaceMembershipOut(BaseModel):
    workspace_id: str
    user_id: str
    role: WorkspaceRoleValue
    joined_at: float


class CreateCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    jurisdiction: str = Field(default="CN", min_length=1, max_length=32)
    scenario_type: str = Field(default="", max_length=100)
    assessment_date: date | None = None
    reviewer_id: str | None = Field(default=None, min_length=1)


class UpdateCaseRequest(BaseModel):
    """PATCH 只更新显式传入字段；`None` 可清空日期或 reviewer。"""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    jurisdiction: str | None = Field(default=None, min_length=1, max_length=32)
    scenario_type: str | None = Field(default=None, max_length=100)
    assessment_date: date | None = None
    reviewer_id: str | None = Field(default=None, min_length=1)


class TransitionCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: CaseStatusValue


class CaseOut(BaseModel):
    case_id: str
    workspace_id: str
    title: str
    description: str
    jurisdiction: str
    scenario_type: str
    assessment_date: date | None
    status: CaseStatusValue
    owner_id: str
    reviewer_id: str | None
    active_assessment_id: str | None
    created_at: float
    updated_at: float


class CaseListResponse(BaseModel):
    cases: list[CaseOut]
