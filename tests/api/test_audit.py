"""``/api/v2/audit/*`` 路由测试（Step 021）。

策略：
- 401 / 403 / 200 三段式权限校验
- admin 调用走 ``container.audit_log``（已注入 ``FakeAuditLogRepo``）
- 通过 ``container.audit_log.entries`` 直接 seed，不经过 use case
- 验证 filter / limit query 参数透传
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.container import AppContainer
from domain.models import AuditAction, AuditEntry


def _login_as_admin(client: TestClient) -> None:
    state = client.get("/api/v2/auth/github/login").json()["state"]
    resp = client.get(
        "/api/v2/auth/github/callback",
        params={"code": "fake-code", "state": state},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text
    assert "copilot_session" in client.cookies


def _seed(client: TestClient, *entries: AuditEntry) -> None:
    container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
    for e in entries:
        container.audit_log.record(e)


# ────────────────────────── auth gating ──────────────────────────


class TestAuthGating:
    def test_unauthed_returns_401(self, client: TestClient) -> None:
        resp = client.get("/api/v2/audit/logs")
        assert resp.status_code == 401
        assert resp.json()["error_code"] == "AUTH_REQUIRED"

    def test_non_admin_returns_403(self, authed_client: tuple[TestClient, dict[str, Any]]) -> None:
        client, _ = authed_client
        resp = client.get("/api/v2/audit/logs")
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "ADMIN_REQUIRED"


# ────────────────────────── admin happy path ──────────────────────────


class _AdminBase:
    @pytest.fixture
    def admin_user_ids(self) -> list[str]:
        return ["github:alice"]

    @pytest.fixture
    def admin_client(self, client: TestClient) -> TestClient:
        _login_as_admin(client)
        # Step 025c：清除登录本身写入的 ``auth.login_success`` 条目，
        # 让后续测试只断言显式 ``_seed`` 的内容。
        container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
        container.audit_log.entries.clear()  # type: ignore[attr-defined]
        return client


class TestList(_AdminBase):
    def test_empty(self, admin_client: TestClient) -> None:
        resp = admin_client.get("/api/v2/audit/logs")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"entries": [], "count": 0}

    def test_returns_entries_descending(self, admin_client: TestClient) -> None:
        _seed(
            admin_client,
            AuditEntry(
                actor_id="github:alice",
                action=AuditAction.KB_DELETE,
                resource="early.pdf",
                timestamp=100.0,
                success=True,
            ),
            AuditEntry(
                actor_id="github:alice",
                action=AuditAction.KB_INGEST_FILE,
                resource="late.pdf",
                timestamp=300.0,
                success=True,
            ),
            AuditEntry(
                actor_id="github:bob",
                action=AuditAction.KB_DELETE,
                resource="mid.pdf",
                timestamp=200.0,
                success=False,
                error="boom",
            ),
        )
        resp = admin_client.get("/api/v2/audit/logs")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 3
        names = [e["resource"] for e in body["entries"]]
        assert names == ["late.pdf", "mid.pdf", "early.pdf"]
        # extra_json / error / success 字段都序列化出来
        mid = next(e for e in body["entries"] if e["resource"] == "mid.pdf")
        assert mid["success"] is False
        assert mid["error"] == "boom"


class TestFilters(_AdminBase):
    def _seed_three(self, client: TestClient) -> None:
        _seed(
            client,
            AuditEntry(
                actor_id="github:alice",
                action=AuditAction.KB_DELETE,
                resource="d1",
                timestamp=100.0,
                success=True,
            ),
            AuditEntry(
                actor_id="github:alice",
                action=AuditAction.KB_INGEST_FILE,
                resource="f1",
                timestamp=200.0,
                success=True,
            ),
            AuditEntry(
                actor_id="github:bob",
                action=AuditAction.KB_DELETE,
                resource="d2",
                timestamp=300.0,
                success=True,
            ),
        )

    def test_filter_by_action(self, admin_client: TestClient) -> None:
        self._seed_three(admin_client)
        resp = admin_client.get("/api/v2/audit/logs", params={"action": "kb.delete"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 2
        assert {e["resource"] for e in body["entries"]} == {"d1", "d2"}

    def test_filter_by_actor(self, admin_client: TestClient) -> None:
        self._seed_three(admin_client)
        resp = admin_client.get("/api/v2/audit/logs", params={"actor_id": "github:alice"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 2
        assert {e["resource"] for e in body["entries"]} == {"d1", "f1"}

    def test_limit_caps_results(self, admin_client: TestClient) -> None:
        self._seed_three(admin_client)
        resp = admin_client.get("/api/v2/audit/logs", params={"limit": 2})
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 2

    def test_limit_validation(self, admin_client: TestClient) -> None:
        resp = admin_client.get("/api/v2/audit/logs", params={"limit": 0})
        assert resp.status_code == 422
        resp2 = admin_client.get("/api/v2/audit/logs", params={"limit": 1000})
        assert resp2.status_code == 422


class TestPagination(_AdminBase):
    def _seed_five(self, client: TestClient) -> None:
        for i in range(5):
            _seed(
                client,
                AuditEntry(
                    actor_id="github:alice",
                    action=AuditAction.KB_DELETE,
                    resource=f"r{i}",
                    timestamp=100.0 + i,  # i=4 最新
                    success=True,
                ),
            )

    def test_offset_default_is_zero(self, admin_client: TestClient) -> None:
        self._seed_five(admin_client)
        resp = admin_client.get("/api/v2/audit/logs", params={"limit": 2})
        body = resp.json()
        assert [e["resource"] for e in body["entries"]] == ["r4", "r3"]

    def test_offset_paginates(self, admin_client: TestClient) -> None:
        self._seed_five(admin_client)
        resp = admin_client.get("/api/v2/audit/logs", params={"limit": 2, "offset": 2})
        assert resp.status_code == 200
        body = resp.json()
        # 倒序 r4 r3 r2 r1 r0；offset=2 拿 r2 r1
        assert [e["resource"] for e in body["entries"]] == ["r2", "r1"]
        assert body["count"] == 2

    def test_offset_beyond_total_returns_empty(self, admin_client: TestClient) -> None:
        self._seed_five(admin_client)
        resp = admin_client.get("/api/v2/audit/logs", params={"limit": 10, "offset": 100})
        body = resp.json()
        assert body == {"entries": [], "count": 0}

    def test_offset_negative_rejected(self, admin_client: TestClient) -> None:
        resp = admin_client.get("/api/v2/audit/logs", params={"offset": -1})
        assert resp.status_code == 422


# ────────────────────────── CSV 导出（Step 026a） ──────────────────────────


class TestExportCsv(_AdminBase):
    """``GET /audit/export.csv``：admin-only · 流式 CSV 下载。"""

    _CSV_PATH = "/api/v2/audit/export.csv"

    def _seed_three(self, client: TestClient) -> None:
        _seed(
            client,
            AuditEntry(
                actor_id="github:alice",
                action=AuditAction.KB_DELETE,
                resource="d1.pdf",
                timestamp=100.0,
                request_id="req-1",
                success=True,
                extra_json={"size": 12},
            ),
            AuditEntry(
                actor_id="github:alice",
                action=AuditAction.KB_INGEST_FILE,
                resource="f1.pdf",
                timestamp=200.0,
                request_id="req-2",
                success=True,
                extra_json={},
            ),
            AuditEntry(
                actor_id="github:bob",
                action=AuditAction.KB_DELETE,
                resource="d2.pdf",
                timestamp=300.0,
                request_id=None,
                success=False,
                error="boom",
                extra_json={"reason": "中文"},
            ),
        )

    def test_unauthed_returns_401(self, client: TestClient) -> None:
        resp = client.get(self._CSV_PATH)
        assert resp.status_code == 401
        assert resp.json()["error_code"] == "AUTH_REQUIRED"

    def test_non_admin_returns_403(self, authed_client: tuple[TestClient, dict[str, Any]]) -> None:
        client, _ = authed_client
        resp = client.get(self._CSV_PATH)
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "ADMIN_REQUIRED"

    def test_admin_returns_csv_with_headers(self, admin_client: TestClient) -> None:
        self._seed_three(admin_client)
        resp = admin_client.get(self._CSV_PATH)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")
        cd = resp.headers["content-disposition"]
        assert cd.startswith("attachment;")
        assert "audit_export_" in cd and ".csv" in cd
        assert resp.headers.get("cache-control") == "no-store"

        body = resp.content
        # BOM 起头
        assert body.startswith(b"\xef\xbb\xbf")
        text = body.decode("utf-8-sig")
        lines = text.strip().splitlines()
        # 表头 + 3 行
        assert len(lines) == 4
        header = lines[0]
        assert header.startswith("timestamp_iso,timestamp_epoch,actor_id,")
        assert header.endswith(",extra_json")
        # 倒序：bob 在最前
        first_data = lines[1]
        assert "github:bob" in first_data
        assert "d2.pdf" in first_data
        # success=False → "0"；error 字段被填
        assert ",0,boom," in first_data
        # 中文 extra_json 不乱码
        assert "中文" in first_data

    def test_filter_by_action(self, admin_client: TestClient) -> None:
        self._seed_three(admin_client)
        resp = admin_client.get(self._CSV_PATH, params={"action": "kb.delete"})
        assert resp.status_code == 200
        text = resp.content.decode("utf-8-sig")
        lines = text.strip().splitlines()
        # 表头 + 2 行（d1 / d2）
        assert len(lines) == 3
        joined = "\n".join(lines[1:])
        assert "d1.pdf" in joined and "d2.pdf" in joined
        assert "f1.pdf" not in joined

    def test_filter_by_actor(self, admin_client: TestClient) -> None:
        self._seed_three(admin_client)
        resp = admin_client.get(self._CSV_PATH, params={"actor_id": "github:alice"})
        assert resp.status_code == 200
        text = resp.content.decode("utf-8-sig")
        lines = text.strip().splitlines()
        assert len(lines) == 3  # alice 名下 2 条
        joined = "\n".join(lines[1:])
        assert "d1.pdf" in joined and "f1.pdf" in joined
        assert "d2.pdf" not in joined

    def test_empty_returns_header_only(self, admin_client: TestClient) -> None:
        resp = admin_client.get(self._CSV_PATH)
        assert resp.status_code == 200
        text = resp.content.decode("utf-8-sig")
        lines = text.strip().splitlines()
        assert len(lines) == 1
        assert lines[0].startswith("timestamp_iso,")

    def test_max_rows_validation(self, admin_client: TestClient) -> None:
        # 0 / 上限+1 都应 422
        resp = admin_client.get(self._CSV_PATH, params={"max_rows": 0})
        assert resp.status_code == 422
        resp2 = admin_client.get(self._CSV_PATH, params={"max_rows": 99999})
        assert resp2.status_code == 422
