"""V2 Workspace 领域模型测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from domain import Workspace, WorkspaceMembership


def _workspace(**overrides: object) -> Workspace:
    values: dict[str, object] = {
        "workspace_id": "ws_001",
        "name": "跨境合规组",
        "created_by": "github:alice",
        "created_at": 100.0,
        "updated_at": 100.0,
    }
    values.update(overrides)
    return Workspace(**values)  # type: ignore[arg-type]


class TestWorkspace:
    def test_defaults_to_active(self) -> None:
        workspace = _workspace()
        assert workspace.status == "active"

    def test_invalid_status_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _workspace(status="deleted")

    def test_updated_at_cannot_precede_created_at(self) -> None:
        with pytest.raises(ValidationError, match="updated_at"):
            _workspace(created_at=200.0, updated_at=100.0)

    def test_is_frozen(self) -> None:
        workspace = _workspace()
        with pytest.raises(ValidationError):
            workspace.name = "被修改"  # type: ignore[misc]

    def test_json_round_trip(self) -> None:
        workspace = _workspace(status="archived")
        assert Workspace.model_validate_json(workspace.model_dump_json()) == workspace


class TestWorkspaceMembership:
    @pytest.mark.parametrize("role", ["viewer", "editor", "reviewer", "admin"])
    def test_accepts_supported_roles(self, role: str) -> None:
        membership = WorkspaceMembership(
            workspace_id="ws_001",
            user_id="github:alice",
            role=role,  # type: ignore[arg-type]
            joined_at=100.0,
        )
        assert membership.role == role

    def test_rejects_unknown_role(self) -> None:
        with pytest.raises(ValidationError):
            WorkspaceMembership(
                workspace_id="ws_001",
                user_id="github:alice",
                role="owner",  # type: ignore[arg-type]
                joined_at=100.0,
            )
