"""V3 案件文档 API 测试。"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.container import AppContainer


def _switch_actor(client: TestClient, actor_id: str) -> None:
    container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
    token = container.auth.issue_jwt(actor_id)
    client.cookies.set(container.settings.cookie_name, token)


def _create_case(client: TestClient) -> tuple[str, str]:
    workspace = client.post(
        "/api/v3/workspaces",
        json={"name": "跨境合规组"},
    ).json()
    case = client.post(
        "/api/v3/cases",
        json={
            "workspace_id": workspace["workspace_id"],
            "title": "海外客服项目",
        },
    ).json()
    return workspace["workspace_id"], case["case_id"]


class TestDocumentUpload:
    def test_requires_authentication(self, client: TestClient) -> None:
        response = client.post(
            "/api/v3/cases/case_x/documents",
            files={"file": ("policy.txt", b"text", "text/plain")},
        )
        assert response.status_code == 401

    def test_upload_returns_queued_job(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, user = authed_client
        _, case_id = _create_case(client)
        response = client.post(
            f"/api/v3/cases/{case_id}/documents",
            files={"file": ("policy.txt", "制度文本".encode(), "text/plain")},
            data={"document_type": "internal_policy", "purpose": "内部制度"},
        )
        assert response.status_code == 202
        body = response.json()
        assert body["document"]["status"] == "queued"
        assert body["document"]["created_by"] == user["user_id"]
        assert body["version"]["mime_type"] == "text/plain"
        assert body["job"]["status"] == "queued"
        assert body["job"]["current_stage"] == "extract_structure"
        assert body["purpose"] == "内部制度"

    def test_fake_pdf_returns_stable_error(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        _, case_id = _create_case(client)
        response = client.post(
            f"/api/v3/cases/{case_id}/documents",
            files={"file": ("fake.pdf", b"not-pdf", "application/pdf")},
        )
        assert response.status_code == 400
        assert response.json()["error_code"] == "INVALID_DOCUMENT_CONTENT"

    def test_unsupported_type_returns_415(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        _, case_id = _create_case(client)
        response = client.post(
            f"/api/v3/cases/{case_id}/documents",
            files={"file": ("script.exe", b"MZ", "application/octet-stream")},
        )
        assert response.status_code == 415
        assert response.json()["error_code"] == "UNSUPPORTED_DOCUMENT_TYPE"

    def test_viewer_cannot_upload(self, authed_client: tuple[TestClient, dict[str, Any]]) -> None:
        client, _ = authed_client
        workspace_id, case_id = _create_case(client)
        client.put(
            f"/api/v3/workspaces/{workspace_id}/members/github:viewer",
            json={"role": "viewer"},
        )
        _switch_actor(client, "github:viewer")
        response = client.post(
            f"/api/v3/cases/{case_id}/documents",
            files={"file": ("policy.txt", b"text", "text/plain")},
        )
        assert response.status_code == 403
        assert response.json()["error_code"] == "WORKSPACE_FORBIDDEN"


class TestDocumentQueries:
    def test_list_detail_download_and_job(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        _, case_id = _create_case(client)
        uploaded = client.post(
            f"/api/v3/cases/{case_id}/documents",
            files={"file": ("policy.md", "# 制度".encode(), "text/markdown")},
            data={"purpose": "内部制度"},
        ).json()
        document_id = uploaded["document"]["document_id"]
        job_id = uploaded["job"]["job_id"]

        listed = client.get(f"/api/v3/cases/{case_id}/documents")
        assert listed.status_code == 200
        assert listed.json()["documents"][0]["document_id"] == document_id

        detail = client.get(f"/api/v3/cases/{case_id}/documents/{document_id}")
        assert detail.status_code == 200
        assert detail.json()["purpose"] == "内部制度"

        content = client.get(f"/api/v3/cases/{case_id}/documents/{document_id}/content")
        assert content.status_code == 200
        assert content.content == "# 制度".encode()
        assert content.headers["content-type"].startswith("text/markdown")
        assert "filename*=UTF-8''" in content.headers["content-disposition"]

        job = client.get(f"/api/v3/processing-jobs/{job_id}")
        assert job.status_code == 200
        assert job.json()["status"] == "queued"

    def test_outsider_gets_404_for_document_and_job(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        _, case_id = _create_case(client)
        uploaded = client.post(
            f"/api/v3/cases/{case_id}/documents",
            files={"file": ("policy.txt", b"text", "text/plain")},
        ).json()
        document_id = uploaded["document"]["document_id"]
        job_id = uploaded["job"]["job_id"]

        _switch_actor(client, "github:outsider")
        detail = client.get(f"/api/v3/cases/{case_id}/documents/{document_id}")
        assert detail.status_code == 404
        assert detail.json()["error_code"] == "CASE_NOT_FOUND"
        job = client.get(f"/api/v3/processing-jobs/{job_id}")
        assert job.status_code == 404
        assert job.json()["error_code"] == "PROCESSING_JOB_NOT_FOUND"
