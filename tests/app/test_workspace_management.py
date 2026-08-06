"""V2 WorkspaceManagementUseCase 测试。"""

from __future__ import annotations

import pytest

from app.use_cases import WorkspaceManagementUseCase
from domain import WorkspaceAccessDenied, WorkspaceNotFound
from tests.fakes import InMemoryWorkspaceRepo


def _uc() -> WorkspaceManagementUseCase:
    return WorkspaceManagementUseCase(InMemoryWorkspaceRepo())


class TestWorkspaceLifecycle:
    def test_creator_becomes_admin(self) -> None:
        uc = _uc()
        workspace = uc.create_workspace("github:alice", name="跨境合规组")
        membership = uc.require_membership(workspace.workspace_id, "github:alice")
        assert membership.role == "admin"

    def test_list_only_returns_joined_workspace(self) -> None:
        uc = _uc()
        uc.create_workspace("github:alice", name="Alice")
        uc.create_workspace("github:bob", name="Bob")
        assert [w.name for w in uc.list_workspaces("github:alice")] == ["Alice"]

    def test_non_member_cannot_read_workspace(self) -> None:
        uc = _uc()
        workspace = uc.create_workspace("github:alice", name="Alice")
        with pytest.raises(WorkspaceNotFound):
            uc.get_workspace(workspace.workspace_id, "github:bob")


class TestWorkspaceMembers:
    def test_admin_can_add_reviewer(self) -> None:
        uc = _uc()
        workspace = uc.create_workspace("github:alice", name="Alice")
        membership = uc.add_or_update_member(
            workspace.workspace_id,
            "github:alice",
            user_id="github:bob",
            role="reviewer",
        )
        assert membership.role == "reviewer"

    def test_non_admin_cannot_manage_members(self) -> None:
        uc = _uc()
        workspace = uc.create_workspace("github:alice", name="Alice")
        uc.add_or_update_member(
            workspace.workspace_id,
            "github:alice",
            user_id="github:bob",
            role="editor",
        )
        with pytest.raises(WorkspaceAccessDenied):
            uc.add_or_update_member(
                workspace.workspace_id,
                "github:bob",
                user_id="github:charlie",
                role="viewer",
            )
