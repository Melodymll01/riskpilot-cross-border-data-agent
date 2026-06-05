"""``/api/v2/documents/*`` 路由测试（Step 016c）。

策略：
- 全部端点 admin-only：未登录 → 401；登录非 admin → 403；admin → 200/201/4xx 业务态
- 业务编排用 ``container.kb_management``（已注入 FakeKbRepo + FakeDocumentLoader）
- 通过 ``container.kb_repo._store`` 直接观察副作用，避免依赖 chroma
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.container import AppContainer
from domain.models import KbChunk
from tests.fakes.fake_document_loader import FakeDocumentLoader


def _login_as_admin(client: TestClient) -> str:
    """走 GitHub fake 流程；颁发 JWT 并把 cookie 留在 client 上。

    注：/auth/me 还会读 user_repo，但 FakeAuth.complete_oauth 不会把 user 写到
    user_repo（auth_login use case 也不写）。``require_admin`` 只校验 JWT，
    不读 user_repo，所以这里只验 cookie 已下发即可。
    """
    state = client.get("/api/v2/auth/github/login").json()["state"]
    resp = client.get(
        "/api/v2/auth/github/callback",
        params={"code": "fake-code", "state": state},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text
    assert "copilot_session" in client.cookies
    return "github:alice"


def _seed_chunks(
    client: TestClient,
    *,
    source: str,
    n: int = 2,
    source_type: str = "file",
    title: str = "",
    url: str | None = None,
    category: str = "",
) -> None:
    """直接走 fake kb_repo + embedder 注入 chunks，绕过 loader。"""
    container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
    chunks = [
        KbChunk(
            chunk_id=f"{source}:{i}",
            text=f"seed-text-{i}",
            source_name=source,
            source_type="web" if source_type == "web" else "file",
            title=title,
            source_url=url,
            chunk_index=i,
            category=category,
        )
        for i in range(n)
    ]
    embeddings = container.embedder.embed([c.text for c in chunks])
    container.kb_repo.add_chunks(chunks, embeddings)


# ────────────────────────────── auth gating ──────────────────────────────


class TestAuthGating:
    """所有端点 admin-only：未登录 401 / 非 admin 403 / admin 放行。"""

    def test_list_requires_auth(self, client: TestClient) -> None:
        resp = client.get("/api/v2/documents")
        assert resp.status_code == 401
        assert resp.json()["error_code"] == "AUTH_REQUIRED"

    def test_list_non_admin_forbidden(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        resp = client.get("/api/v2/documents")
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "ADMIN_REQUIRED"

    def test_get_requires_auth(self, client: TestClient) -> None:
        resp = client.get("/api/v2/documents/foo.pdf")
        assert resp.status_code == 401

    def test_delete_requires_auth(self, client: TestClient) -> None:
        resp = client.delete("/api/v2/documents/foo.pdf")
        assert resp.status_code == 401

    def test_ingest_file_requires_auth(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v2/documents/file",
            files={"file": ("a.txt", b"hi", "text/plain")},
        )
        assert resp.status_code == 401

    def test_ingest_web_requires_auth(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v2/documents/web", json={"url": "https://x.com"}
        )
        assert resp.status_code == 401


# ───────────────────────── admin-only fixture ───────────────────────────


class _AdminBase:
    """所有 admin 用例的共享 fixture：admin_user_ids=[github:alice]。"""

    @pytest.fixture
    def admin_user_ids(self) -> list[str]:
        return ["github:alice"]

    @pytest.fixture
    def admin_client(self, client: TestClient) -> TestClient:
        _login_as_admin(client)
        return client


# ────────────────────────────── list / get / stats ──────────────────────


class TestList(_AdminBase):
    def test_empty_kb(self, admin_client: TestClient) -> None:
        resp = admin_client.get("/api/v2/documents")
        assert resp.status_code == 200
        assert resp.json() == {"documents": [], "total_chunks": 0}

    def test_lists_seeded_documents(self, admin_client: TestClient) -> None:
        _seed_chunks(admin_client, source="a.pdf", n=3, title="A")
        _seed_chunks(admin_client, source="b.txt", n=2)
        resp = admin_client.get("/api/v2/documents")
        assert resp.status_code == 200
        body = resp.json()
        names = {d["source_name"]: d for d in body["documents"]}
        assert set(names) == {"a.pdf", "b.txt"}
        assert names["a.pdf"]["chunk_count"] == 3
        assert names["a.pdf"]["title"] == "A"
        assert body["total_chunks"] == 5


class TestStats(_AdminBase):
    def test_stats_zero(self, admin_client: TestClient) -> None:
        resp = admin_client.get("/api/v2/documents/stats")
        assert resp.status_code == 200
        assert resp.json() == {"document_count": 0, "chunk_count": 0}

    def test_stats_after_seed(self, admin_client: TestClient) -> None:
        _seed_chunks(admin_client, source="a.pdf", n=3)
        _seed_chunks(admin_client, source="b.txt", n=2)
        resp = admin_client.get("/api/v2/documents/stats")
        assert resp.status_code == 200
        assert resp.json() == {"document_count": 2, "chunk_count": 5}


class TestGet(_AdminBase):
    def test_get_hit(self, admin_client: TestClient) -> None:
        _seed_chunks(
            admin_client, source="hit.pdf", n=2, title="T", category="法规"
        )
        resp = admin_client.get("/api/v2/documents/hit.pdf")
        assert resp.status_code == 200
        body = resp.json()
        assert body["source_name"] == "hit.pdf"
        assert body["title"] == "T"
        assert body["category"] == "法规"
        assert body["chunk_count"] == 2

    def test_get_miss_returns_404(self, admin_client: TestClient) -> None:
        resp = admin_client.get("/api/v2/documents/missing.pdf")
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "DOCUMENT_NOT_FOUND"


# ────────────────────────────── delete ─────────────────────────────────


class TestDelete(_AdminBase):
    def test_delete_hit_returns_count(self, admin_client: TestClient) -> None:
        _seed_chunks(admin_client, source="del.pdf", n=4)
        resp = admin_client.delete("/api/v2/documents/del.pdf")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["source_name"] == "del.pdf"
        assert body["deleted_count"] == 4
        # 副作用：再列就空了
        listing = admin_client.get("/api/v2/documents").json()
        assert listing["documents"] == []

    def test_delete_miss_returns_404(self, admin_client: TestClient) -> None:
        resp = admin_client.delete("/api/v2/documents/never.pdf")
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "DOCUMENT_NOT_FOUND"


# ────────────────────────────── ingest file ────────────────────────────


class TestIngestFile(_AdminBase):
    def test_upload_happy_path(self, admin_client: TestClient) -> None:
        container: AppContainer = admin_client.app.state.container  # type: ignore[attr-defined]
        loader: FakeDocumentLoader = container.document_loader  # type: ignore[assignment]

        resp = admin_client.post(
            "/api/v2/documents/file?category=%E6%B3%95%E8%A7%84",
            files={"file": ("policy.txt", b"hello world", "text/plain")},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["success"] is True
        # FakeDocumentLoader 默认返回 2 个 chunk
        assert body["chunk_count"] == 2
        # loader 被调用一次，category 透传，original_filename 传入
        assert len(loader.calls) == 1
        method, _args, kwargs = loader.calls[0]
        assert method == "load_file"
        assert kwargs == {"original_filename": "policy.txt", "category": "法规"}
        # 副作用：repo 里有 2 个 chunk
        assert container.kb_repo.count_chunks() == 2

    def test_upload_rejects_unsupported_ext(
        self, admin_client: TestClient
    ) -> None:
        resp = admin_client.post(
            "/api/v2/documents/file",
            files={"file": ("evil.exe", b"x", "application/octet-stream")},
        )
        assert resp.status_code == 400
        assert resp.json()["error_code"] == "UNSUPPORTED_FILE_TYPE"

    def test_upload_rejects_empty_extension(
        self, admin_client: TestClient
    ) -> None:
        resp = admin_client.post(
            "/api/v2/documents/file",
            files={"file": ("noext", b"x", "application/octet-stream")},
        )
        assert resp.status_code == 400
        assert resp.json()["error_code"] == "UNSUPPORTED_FILE_TYPE"

    def test_upload_rejects_oversize(self, admin_client: TestClient) -> None:
        # default max_upload_mb=50；造 51MB
        container: AppContainer = admin_client.app.state.container  # type: ignore[attr-defined]
        oversize = b"x" * ((container.settings.max_upload_mb + 1) * 1024 * 1024)
        resp = admin_client.post(
            "/api/v2/documents/file",
            files={"file": ("big.txt", oversize, "text/plain")},
        )
        assert resp.status_code == 413
        assert resp.json()["error_code"] == "FILE_TOO_LARGE"

    def test_upload_temp_file_cleaned_on_success(
        self, admin_client: TestClient
    ) -> None:
        container: AppContainer = admin_client.app.state.container  # type: ignore[attr-defined]
        upload_dir = container.settings.upload_dir
        admin_client.post(
            "/api/v2/documents/file",
            files={"file": ("clean.txt", b"hi", "text/plain")},
        )
        # 路由 finally 块清理 → upload_dir 中应没有残留
        import os

        assert os.listdir(upload_dir) == [] or all(
            not name.endswith(".txt") for name in os.listdir(upload_dir)
        )

    def test_upload_empty_doc_returns_success_false(
        self, admin_client: TestClient
    ) -> None:
        """loader 返回空 → use case 返回 success=False，但 HTTP 200/201。"""
        container: AppContainer = admin_client.app.state.container  # type: ignore[attr-defined]
        container.document_loader = FakeDocumentLoader(empty=True)
        # use case 持有的是构造时的引用，需要重建
        from app.use_cases.kb_management import KbManagementUseCase

        container.kb_management = KbManagementUseCase(
            kb_repo=container.kb_repo,
            loader=container.document_loader,
            embedder=container.embedder,
        )

        resp = admin_client.post(
            "/api/v2/documents/file",
            files={"file": ("empty.txt", b"", "text/plain")},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["success"] is False
        assert body["chunk_count"] == 0
        assert "为空" in body["message"]


# ────────────────────────────── ingest web ─────────────────────────────


class TestIngestWeb(_AdminBase):
    def test_web_happy_path(self, admin_client: TestClient) -> None:
        container: AppContainer = admin_client.app.state.container  # type: ignore[attr-defined]
        loader: FakeDocumentLoader = container.document_loader  # type: ignore[assignment]

        resp = admin_client.post(
            "/api/v2/documents/web",
            json={"url": "https://example.com/a", "category": "指南"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["success"] is True
        assert body["chunk_count"] == 2
        assert body["source_name"] == "https://example.com/a"
        # loader 调用透传 category
        assert loader.calls[-1] == (
            "load_web",
            ("https://example.com/a",),
            {"category": "指南"},
        )
        assert container.kb_repo.count_chunks() == 2

    def test_web_rejects_invalid_scheme(self, admin_client: TestClient) -> None:
        resp = admin_client.post(
            "/api/v2/documents/web", json={"url": "ftp://x.com"}
        )
        # pydantic 校验失败 → 422
        assert resp.status_code == 422

    def test_web_rejects_missing_domain(self, admin_client: TestClient) -> None:
        resp = admin_client.post(
            "/api/v2/documents/web", json={"url": "http://"}
        )
        assert resp.status_code == 422

    def test_web_empty_url_field_rejected(
        self, admin_client: TestClient
    ) -> None:
        resp = admin_client.post("/api/v2/documents/web", json={"url": ""})
        assert resp.status_code == 422

    def test_web_empty_doc_returns_success_false(
        self, admin_client: TestClient
    ) -> None:
        container: AppContainer = admin_client.app.state.container  # type: ignore[attr-defined]
        container.document_loader = FakeDocumentLoader(empty=True)
        from app.use_cases.kb_management import KbManagementUseCase

        container.kb_management = KbManagementUseCase(
            kb_repo=container.kb_repo,
            loader=container.document_loader,
            embedder=container.embedder,
        )

        resp = admin_client.post(
            "/api/v2/documents/web", json={"url": "https://empty.example.com"}
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["success"] is False
        assert "为空" in body["message"]
