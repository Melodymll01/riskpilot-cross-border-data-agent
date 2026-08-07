"""V3 Case API 测试。"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.container import AppContainer


def _switch_actor(client: TestClient, actor_id: str) -> None:
    container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
    token = container.auth.issue_jwt(actor_id)
    client.cookies.set(container.settings.cookie_name, token)


def _create_workspace(client: TestClient, name: str = "跨境合规组") -> str:
    response = client.post("/api/v3/workspaces", json={"name": name})
    assert response.status_code == 201
    return response.json()["workspace_id"]


def _add_member(
    client: TestClient,
    workspace_id: str,
    user_id: str,
    role: str,
) -> None:
    response = client.put(
        f"/api/v3/workspaces/{workspace_id}/members/{user_id}",
        json={"role": role},
    )
    assert response.status_code == 200


def _create_case(client: TestClient, workspace_id: str, title: str = "海外客服项目") -> dict:
    response = client.post(
        "/api/v3/cases",
        json={
            "workspace_id": workspace_id,
            "title": title,
            "description": "客服数据将在境外处理",
            "scenario_type": "personal_information",
            "assessment_date": "2026-08-06",
        },
    )
    assert response.status_code == 201
    return response.json()


class TestCaseAuthentication:
    def test_requires_authentication(self, client: TestClient) -> None:
        response = client.get("/api/v3/cases", params={"workspace_id": "ws_x"})
        assert response.status_code == 401


class TestCaseLifecycle:
    def test_create_list_get_and_update(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, user = authed_client
        workspace_id = _create_workspace(client)
        case = _create_case(client, workspace_id)
        assert case["case_id"].startswith("case_")
        assert case["owner_id"] == user["user_id"]
        assert case["status"] == "draft"
        assert case["assessment_date"] == "2026-08-06"

        listed = client.get(
            "/api/v3/cases",
            params={"workspace_id": workspace_id},
        )
        assert listed.status_code == 200
        assert listed.json()["cases"] == [case]

        detail = client.get(f"/api/v3/cases/{case['case_id']}")
        assert detail.status_code == 200
        assert detail.json() == case

        updated = client.patch(
            f"/api/v3/cases/{case['case_id']}",
            json={
                "title": "海外客服数据出境评估",
                "assessment_date": None,
            },
        )
        assert updated.status_code == 200
        assert updated.json()["title"] == "海外客服数据出境评估"
        assert updated.json()["assessment_date"] is None

    def test_non_member_cannot_discover_case(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        workspace_id = _create_workspace(client)
        case = _create_case(client, workspace_id)

        _switch_actor(client, "github:outsider")
        response = client.get(f"/api/v3/cases/{case['case_id']}")
        assert response.status_code == 404
        assert response.json()["error_code"] == "CASE_NOT_FOUND"

    def test_viewer_cannot_create_case(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        workspace_id = _create_workspace(client)
        _add_member(client, workspace_id, "github:viewer", "viewer")

        _switch_actor(client, "github:viewer")
        response = client.post(
            "/api/v3/cases",
            json={"workspace_id": workspace_id, "title": "越权案件"},
        )
        assert response.status_code == 403
        assert response.json()["error_code"] == "WORKSPACE_FORBIDDEN"

    def test_patch_rejects_state_field(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        workspace_id = _create_workspace(client)
        case = _create_case(client, workspace_id)
        response = client.patch(
            f"/api/v3/cases/{case['case_id']}",
            json={"status": "completed"},
        )
        assert response.status_code == 422


class TestCaseTransitions:
    def test_invalid_transition_returns_conflict(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        workspace_id = _create_workspace(client)
        case = _create_case(client, workspace_id)

        response = client.post(
            f"/api/v3/cases/{case['case_id']}/transitions",
            json={"target": "completed"},
        )
        assert response.status_code == 409
        assert response.json()["error_code"] == "INVALID_CASE_TRANSITION"

    def test_case_completion_requires_assessment_approval(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        workspace_id = _create_workspace(client)
        _add_member(client, workspace_id, "github:editor", "editor")
        _add_member(client, workspace_id, "github:reviewer", "reviewer")

        _switch_actor(client, "github:editor")
        case = _create_case(client, workspace_id)
        for target in (
            "collecting",
            "ready_for_assessment",
            "assessing",
            "review_required",
        ):
            response = client.post(
                f"/api/v3/cases/{case['case_id']}/transitions",
                json={"target": target},
            )
            assert response.status_code == 200

        editor_response = client.post(
            f"/api/v3/cases/{case['case_id']}/transitions",
            json={"target": "completed"},
        )
        assert editor_response.status_code == 400
        assert "Assessment" in editor_response.json()["message"]

        _switch_actor(client, "github:reviewer")
        reviewer_response = client.post(
            f"/api/v3/cases/{case['case_id']}/transitions",
            json={"target": "completed"},
        )
        assert reviewer_response.status_code == 400
        assert "Assessment" in reviewer_response.json()["message"]
        bypass_response = client.post(
            f"/api/v3/cases/{case['case_id']}/transitions",
            json={"target": "ready_for_assessment"},
        )
        assert bypass_response.status_code == 400
        assert "Assessment" in bypass_response.json()["message"]

    def test_archived_case_rejects_updates(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        workspace_id = _create_workspace(client)
        case = _create_case(client, workspace_id)
        archived = client.post(
            f"/api/v3/cases/{case['case_id']}/transitions",
            json={"target": "archived"},
        )
        assert archived.status_code == 200

        response = client.patch(
            f"/api/v3/cases/{case['case_id']}",
            json={"title": "归档后修改"},
        )
        assert response.status_code == 409
        assert response.json()["error_code"] == "CASE_ARCHIVED"

        repeated = client.post(
            f"/api/v3/cases/{case['case_id']}/transitions",
            json={"target": "archived"},
        )
        assert repeated.status_code == 200
        assert repeated.json()["status"] == "archived"
