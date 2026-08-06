"""V3 Workspace API 测试。"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.container import AppContainer


def _switch_actor(client: TestClient, actor_id: str) -> None:
    container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
    token = container.auth.issue_jwt(actor_id)
    client.cookies.set(container.settings.cookie_name, token)


class TestWorkspaceAuthentication:
    def test_requires_authentication(self, client: TestClient) -> None:
        response = client.get("/api/v3/workspaces")
        assert response.status_code == 401
        assert response.json()["error_code"] == "AUTH_REQUIRED"


class TestWorkspaceLifecycle:
    def test_create_list_and_get(self, authed_client: tuple[TestClient, dict[str, Any]]) -> None:
        client, user = authed_client
        created = client.post(
            "/api/v3/workspaces",
            json={"name": "跨境合规组"},
        )
        assert created.status_code == 201
        workspace = created.json()
        assert workspace["workspace_id"].startswith("ws_")
        assert workspace["created_by"] == user["user_id"]
        assert workspace["status"] == "active"

        listed = client.get("/api/v3/workspaces")
        assert listed.status_code == 200
        assert listed.json()["workspaces"] == [workspace]

        detail = client.get(f"/api/v3/workspaces/{workspace['workspace_id']}")
        assert detail.status_code == 200
        assert detail.json() == workspace

    def test_rejects_extra_fields(self, authed_client: tuple[TestClient, dict[str, Any]]) -> None:
        client, _ = authed_client
        response = client.post(
            "/api/v3/workspaces",
            json={"name": "跨境合规组", "role": "admin"},
        )
        assert response.status_code == 422

    def test_non_member_sees_404(self, authed_client: tuple[TestClient, dict[str, Any]]) -> None:
        client, _ = authed_client
        workspace_id = client.post(
            "/api/v3/workspaces",
            json={"name": "私有工作空间"},
        ).json()["workspace_id"]

        _switch_actor(client, "github:outsider")
        response = client.get(f"/api/v3/workspaces/{workspace_id}")
        assert response.status_code == 404
        assert response.json()["error_code"] == "WORKSPACE_NOT_FOUND"


class TestWorkspaceMembers:
    def test_admin_can_add_reviewer(self, authed_client: tuple[TestClient, dict[str, Any]]) -> None:
        client, _ = authed_client
        workspace_id = client.post(
            "/api/v3/workspaces",
            json={"name": "跨境合规组"},
        ).json()["workspace_id"]

        response = client.put(
            f"/api/v3/workspaces/{workspace_id}/members/github:reviewer",
            json={"role": "reviewer"},
        )
        assert response.status_code == 200
        assert response.json()["role"] == "reviewer"

        _switch_actor(client, "github:reviewer")
        detail = client.get(f"/api/v3/workspaces/{workspace_id}")
        assert detail.status_code == 200

    def test_non_admin_cannot_manage_members(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        workspace_id = client.post(
            "/api/v3/workspaces",
            json={"name": "跨境合规组"},
        ).json()["workspace_id"]
        client.put(
            f"/api/v3/workspaces/{workspace_id}/members/github:editor",
            json={"role": "editor"},
        )

        _switch_actor(client, "github:editor")
        response = client.put(
            f"/api/v3/workspaces/{workspace_id}/members/github:other",
            json={"role": "viewer"},
        )
        assert response.status_code == 403
        assert response.json()["error_code"] == "WORKSPACE_FORBIDDEN"
