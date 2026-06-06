"""端到端：HTTP request_id middleware → contextvar → AuditEntry（Step 025d）。

策略：
- 通过 TestClient（conftest 已挂 ``install_request_id_middleware``）发请求
- 验证两件事：
  (1) response 回写 ``X-Request-ID``，且与 request header 一致（或自动生成）
  (2) 审计条目的 ``request_id`` 字段 = response 头里的 id
- 用 anonymous 登录端点触发审计（已在 Step 025c 接入 ``AUTH_ANONYMOUS_CREATE``）
- 不依赖 KB 端点（避免文件 IO），登录端点已足够验证链路
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.container import AppContainer
from domain.models import AuditAction


def _container_audit_entries(client: TestClient) -> list:
    container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
    return container.audit_log.entries  # type: ignore[attr-defined]


class TestRequestIdEcho:
    def test_response_echoes_explicit_request_id(self, client: TestClient) -> None:
        resp = client.get("/api/v2/health", headers={"X-Request-ID": "req-test-1"})
        assert resp.status_code == 200
        assert resp.headers["X-Request-ID"] == "req-test-1"

    def test_response_generates_request_id_when_missing(self, client: TestClient) -> None:
        resp = client.get("/api/v2/health")
        assert resp.status_code == 200
        rid = resp.headers.get("X-Request-ID")
        assert rid, "缺失 X-Request-ID 响应头"
        assert len(rid) >= 8  # uuid4().hex[:12] 12 字符；至少 8


class TestRequestIdPropagatesToAudit:
    def test_anonymous_login_audit_carries_request_id(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v2/auth/anonymous",
            headers={"X-Request-ID": "req-anon-1"},
        )
        assert resp.status_code == 201
        assert resp.headers["X-Request-ID"] == "req-anon-1"

        entries = _container_audit_entries(client)
        anon = [e for e in entries if e.action == AuditAction.AUTH_ANONYMOUS_CREATE]
        assert len(anon) == 1
        assert anon[0].request_id == "req-anon-1"

    def test_auto_generated_request_id_appears_in_audit(self, client: TestClient) -> None:
        resp = client.post("/api/v2/auth/anonymous")  # 无 header
        assert resp.status_code == 201
        rid = resp.headers["X-Request-ID"]

        entries = _container_audit_entries(client)
        anon = [e for e in entries if e.action == AuditAction.AUTH_ANONYMOUS_CREATE]
        assert len(anon) == 1
        assert anon[0].request_id == rid  # 与回写头一致

    def test_distinct_requests_carry_distinct_ids(self, client: TestClient) -> None:
        # 三次匿名登录 → 三条审计 → 三个不同 request_id
        rids: list[str] = []
        for _ in range(3):
            r = client.post("/api/v2/auth/anonymous")
            assert r.status_code == 201
            rids.append(r.headers["X-Request-ID"])
        assert len(set(rids)) == 3

        entries = _container_audit_entries(client)
        anon = [e for e in entries if e.action == AuditAction.AUTH_ANONYMOUS_CREATE]
        assert len(anon) == 3
        # 审计里的 request_id 集合与 response 头集合相同
        assert {e.request_id for e in anon} == set(rids)

    def test_failed_oauth_callback_audit_also_has_request_id(
        self, client: TestClient
    ) -> None:
        # 不先 begin 拿 state，直接 callback → OAuthFlowError → AUTH_LOGIN_FAILURE
        resp = client.get(
            "/api/v2/auth/github/callback",
            params={"code": "fake", "state": "never-issued"},
            headers={"X-Request-ID": "req-fail-x"},
            follow_redirects=False,
        )
        # 错误码非 200，但 middleware 仍要写回 header + contextvar 仍被填
        assert resp.headers["X-Request-ID"] == "req-fail-x"

        entries = _container_audit_entries(client)
        fails = [e for e in entries if e.action == AuditAction.AUTH_LOGIN_FAILURE]
        assert len(fails) == 1
        assert fails[0].request_id == "req-fail-x"
        assert fails[0].success is False
