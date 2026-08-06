"""V2 Workspace 应用用例。"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING

from domain.errors import WorkspaceAccessDenied, WorkspaceNotFound
from domain.workspaces import Workspace, WorkspaceMembership, WorkspaceRole

if TYPE_CHECKING:
    from domain.ports import WorkspaceRepoPort


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class WorkspaceManagementUseCase:
    """Workspace 创建、查询和成员角色管理。"""

    def __init__(self, repo: WorkspaceRepoPort) -> None:
        self._repo = repo

    def create_workspace(self, actor_id: str, *, name: str) -> Workspace:
        if not actor_id:
            raise ValueError("actor_id 必填")
        now = time.time()
        workspace = Workspace(
            workspace_id=_new_id("ws"),
            name=name,
            created_by=actor_id,
            created_at=now,
            updated_at=now,
        )
        membership = WorkspaceMembership(
            workspace_id=workspace.workspace_id,
            user_id=actor_id,
            role="admin",
            joined_at=now,
        )
        self._repo.create(workspace, membership)
        return workspace

    def list_workspaces(self, actor_id: str, *, limit: int = 50) -> list[Workspace]:
        if not actor_id:
            raise ValueError("actor_id 必填")
        return self._repo.list_for_user(actor_id, limit=limit)

    def get_workspace(self, workspace_id: str, actor_id: str) -> Workspace:
        self.require_membership(workspace_id, actor_id)
        workspace = self._repo.get(workspace_id)
        if workspace is None:
            raise WorkspaceNotFound(workspace_id)
        return workspace

    def add_or_update_member(
        self,
        workspace_id: str,
        actor_id: str,
        *,
        user_id: str,
        role: WorkspaceRole,
    ) -> WorkspaceMembership:
        self.require_role(workspace_id, actor_id, {"admin"}, action="管理成员")
        existing = self._repo.get_membership(workspace_id, user_id)
        membership = WorkspaceMembership(
            workspace_id=workspace_id,
            user_id=user_id,
            role=role,
            joined_at=existing.joined_at if existing is not None else time.time(),
        )
        self._repo.upsert_membership(membership)
        return membership

    def require_membership(self, workspace_id: str, actor_id: str) -> WorkspaceMembership:
        membership = self._repo.get_membership(workspace_id, actor_id)
        if membership is None:
            raise WorkspaceNotFound(workspace_id)
        return membership

    def require_role(
        self,
        workspace_id: str,
        actor_id: str,
        allowed_roles: set[WorkspaceRole],
        *,
        action: str,
    ) -> WorkspaceMembership:
        membership = self.require_membership(workspace_id, actor_id)
        if membership.role not in allowed_roles:
            raise WorkspaceAccessDenied(workspace_id, actor_id, action)
        return membership
