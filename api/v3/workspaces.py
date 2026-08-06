"""V3 Workspace 资源路由。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query, status

from api.v2.deps import make_require_owner
from api.v3.schemas import (
    CreateWorkspaceRequest,
    UpsertWorkspaceMemberRequest,
    WorkspaceListResponse,
    WorkspaceMembershipOut,
    WorkspaceOut,
)

if TYPE_CHECKING:
    from app.container import AppContainer
    from domain.workspaces import Workspace, WorkspaceMembership


def _to_workspace_out(workspace: Workspace) -> WorkspaceOut:
    return WorkspaceOut(
        workspace_id=workspace.workspace_id,
        name=workspace.name,
        status=workspace.status,
        created_by=workspace.created_by,
        created_at=workspace.created_at,
        updated_at=workspace.updated_at,
    )


def _to_membership_out(membership: WorkspaceMembership) -> WorkspaceMembershipOut:
    return WorkspaceMembershipOut(
        workspace_id=membership.workspace_id,
        user_id=membership.user_id,
        role=membership.role,
        joined_at=membership.joined_at,
    )


def build_workspace_routes(container: AppContainer) -> APIRouter:
    router = APIRouter(prefix="/workspaces", tags=["v3-workspaces"])
    require_owner = make_require_owner(container)

    @router.post(
        "",
        response_model=WorkspaceOut,
        status_code=status.HTTP_201_CREATED,
        summary="创建 Workspace；创建者自动成为 admin",
    )
    def create_workspace(
        body: CreateWorkspaceRequest,
        actor_id: str = Depends(require_owner),
    ) -> WorkspaceOut:
        workspace = container.workspace_management.create_workspace(
            actor_id,
            name=body.name,
        )
        return _to_workspace_out(workspace)

    @router.get(
        "",
        response_model=WorkspaceListResponse,
        summary="列出当前用户加入的 Workspace",
    )
    def list_workspaces(
        limit: int = Query(default=50, ge=1, le=200),
        actor_id: str = Depends(require_owner),
    ) -> WorkspaceListResponse:
        workspaces = container.workspace_management.list_workspaces(
            actor_id,
            limit=limit,
        )
        return WorkspaceListResponse(
            workspaces=[_to_workspace_out(workspace) for workspace in workspaces]
        )

    @router.get(
        "/{workspace_id}",
        response_model=WorkspaceOut,
        summary="获取当前用户可见的 Workspace",
    )
    def get_workspace(
        workspace_id: str,
        actor_id: str = Depends(require_owner),
    ) -> WorkspaceOut:
        workspace = container.workspace_management.get_workspace(
            workspace_id,
            actor_id,
        )
        return _to_workspace_out(workspace)

    @router.put(
        "/{workspace_id}/members/{user_id}",
        response_model=WorkspaceMembershipOut,
        summary="新增成员或更新成员角色（admin）",
    )
    def upsert_member(
        workspace_id: str,
        user_id: str,
        body: UpsertWorkspaceMemberRequest,
        actor_id: str = Depends(require_owner),
    ) -> WorkspaceMembershipOut:
        membership = container.workspace_management.add_or_update_member(
            workspace_id,
            actor_id,
            user_id=user_id,
            role=body.role,
        )
        return _to_membership_out(membership)

    return router
