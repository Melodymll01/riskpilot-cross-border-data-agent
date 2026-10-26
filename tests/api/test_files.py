"""用户附件 API 测试。"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.container import AppContainer


def _switch_actor(client: TestClient, actor_id: str) -> None:
    container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
    token = container.auth.issue_jwt(actor_id)
    client.cookies.set(container.settings.cookie_name, token)


class TestDocumentUpload:
    def test_requires_authentication(self, client: TestClient) -> None:
        response = client.post(
            "/api/v2/files",
            files={"file": ("policy.txt", b"text", "text/plain")},
        )
        assert response.status_code == 401

    def test_upload_returns_queued_job(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, user = authed_client
        response = client.post(
            "/api/v2/files",
            files={"file": ("policy.txt", "制度文本".encode(), "text/plain")},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["document"]["status"] == "queued"
        assert body["document"]["created_by"] == user["user_id"]
        assert body["version"]["mime_type"] == "text/plain"
        assert body["job"]["status"] == "queued"
        assert body["job"]["current_stage"] == "extract_structure"
        assert body["job"]["revision"] == 0

    def test_fake_pdf_returns_stable_error(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        response = client.post(
            "/api/v2/files",
            files={"file": ("fake.pdf", b"not-pdf", "application/pdf")},
        )
        assert response.status_code == 400
        assert response.json()["error_code"] == "INVALID_DOCUMENT_CONTENT"

    def test_unsupported_type_returns_415(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        response = client.post(
            "/api/v2/files",
            files={"file": ("script.exe", b"MZ", "application/octet-stream")},
        )
        assert response.status_code == 415
        assert response.json()["error_code"] == "UNSUPPORTED_DOCUMENT_TYPE"


class TestDocumentQueries:
    def test_list_detail_download_and_job(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        uploaded = client.post(
            "/api/v2/files",
            files={"file": ("policy.md", "# 制度".encode(), "text/markdown")},
        ).json()
        document_id = uploaded["document"]["document_id"]
        job_id = uploaded["job"]["job_id"]

        listed = client.get("/api/v2/files")
        assert listed.status_code == 200
        listed_document = listed.json()["documents"][0]
        assert listed_document["document"]["document_id"] == document_id
        assert listed_document["job"]["job_id"] == job_id
        assert listed_document["job"]["status"] == "queued"

        detail = client.get(f"/api/v2/files/{document_id}")
        assert detail.status_code == 200
        assert detail.json()["job"]["job_id"] == job_id

        content = client.get(f"/api/v2/files/{document_id}/download")
        assert content.status_code == 200
        assert content.content == "# 制度".encode()
        assert content.headers["content-type"].startswith("text/markdown")
        assert "filename*=UTF-8''" in content.headers["content-disposition"]

        job = client.get(f"/api/v2/files/jobs/{job_id}")
        assert job.status_code == 200
        assert job.json()["status"] == "queued"

        container = client.app.state.container
        owner_id = container.auth_login.identify(client.cookies.get(container.settings.cookie_name))
        parsed = container.document_management.run_parse_stage(job_id, owner_id)
        assert parsed.job.status == "running"
        assert parsed.job.current_stage == "chunk"
        assert parsed.document.status == "chunking"
        indexed = container.document_management.run_index_stage(job_id, owner_id)
        assert indexed.job.status == "completed"
        assert indexed.document.status == "ready"
        assert len(indexed.chunks) >= 1

        restored = client.get("/api/v2/files").json()
        restored_document = restored["documents"][0]
        assert restored_document["document"]["status"] == "ready"
        assert restored_document["job"]["status"] == "completed"
        assert restored_document["job"]["current_stage"] == "ready"

        evidence = client.get(
            "/api/v2/files/search",
            params={"query": "制度"},
        )
        assert evidence.status_code == 200
        assert evidence.json()["hits"][0]["chunk"]["document_id"] == document_id
        assert evidence.json()["hits"][0]["chunk"]["page_number"] == 1

    def test_outsider_gets_404_for_document_and_job(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        uploaded = client.post(
            "/api/v2/files",
            files={"file": ("policy.txt", b"text", "text/plain")},
        ).json()
        document_id = uploaded["document"]["document_id"]
        job_id = uploaded["job"]["job_id"]

        _switch_actor(client, "github:outsider")
        detail = client.get(f"/api/v2/files/{document_id}")
        assert detail.status_code == 404
        assert detail.json()["error_code"] == "DOCUMENT_NOT_FOUND"
        job = client.get(f"/api/v2/files/jobs/{job_id}")
        assert job.status_code == 404
        assert job.json()["error_code"] == "PROCESSING_JOB_NOT_FOUND"


class TestProcessingActions:
    def test_hash_mismatch_fails_then_retry_resets_queued(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        uploaded = client.post(
            "/api/v2/files",
            files={"file": ("policy.txt", b"text", "text/plain")},
        ).json()
        document_id = uploaded["document"]["document_id"]
        job_id = uploaded["job"]["job_id"]
        container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
        version = container.document_repo.get_version(uploaded["version"]["version_id"])
        assert version is not None
        container.object_store.objects[version.object_key] = b"tampered"  # type: ignore[attr-defined]

        import pytest

        from domain.errors import InvalidDocumentContent

        with pytest.raises(InvalidDocumentContent):
            container.document_processing_worker.run_parse_stage(job_id)
        job = client.get(f"/api/v2/files/jobs/{job_id}")
        assert job.json()["status"] == "failed"

        retried = client.post(f"/api/v2/files/jobs/{job_id}/retry")
        assert retried.status_code == 200
        assert retried.json()["status"] == "queued"
        assert retried.json()["retry_count"] == 1
        detail = client.get(f"/api/v2/files/{document_id}")
        assert detail.json()["document"]["status"] == "queued"
        assert detail.json()["job"]["job_id"] == job_id
        assert detail.json()["job"]["status"] == "queued"

    def test_list_and_cancel_processing_job(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        uploaded = client.post(
            "/api/v2/files",
            files={"file": ("policy.txt", b"text", "text/plain")},
        ).json()
        job_id = uploaded["job"]["job_id"]

        listed = client.get("/api/v2/files")
        cancelled = client.post(f"/api/v2/files/jobs/{job_id}/cancel")

        assert listed.status_code == 200
        assert listed.json()["documents"][0]["job"]["job_id"] == job_id
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        assert cancelled.json()["revision"] == 1


class TestEvidenceIsolation:
    def test_search_never_leaks_other_case_chunks(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        container = client.app.state.container
        first = client.post(
            "/api/v2/files", files={"file": ("a.txt", "普通说明".encode(), "text/plain")}
        ).json()
        container.document_pipeline.run(first["job"]["job_id"])
        original_cookie = client.cookies.get(container.settings.cookie_name)
        _switch_actor(client, "github:other")
        second = client.post(
            "/api/v2/files", files={"file": ("b.txt", "境外接收方承担责任".encode(), "text/plain")}
        ).json()
        container.document_pipeline.run(second["job"]["job_id"])
        client.cookies.set(container.settings.cookie_name, original_cookie)
        response = client.get("/api/v2/files/search", params={"query": "境外接收方承担责任"})
        assert response.status_code == 200
        hits = response.json()["hits"]
        assert hits
        assert all(hit["chunk"]["document_id"] == first["document"]["document_id"] for hit in hits)
