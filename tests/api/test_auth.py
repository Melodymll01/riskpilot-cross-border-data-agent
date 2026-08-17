"""``/api/v2/auth/*`` 路由测试。"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient

from api.v2.deps import make_require_admin
from app.container import AppContainer


class TestAnonymousLogin:
    def test_creates_user_and_sets_cookie(self, client: TestClient) -> None:
        resp = client.post("/api/v2/auth/anonymous")
        assert resp.status_code == 201
        body = resp.json()
        assert body["user"]["user_id"].startswith("anon:")
        assert body["user"]["provider"] == "anonymous"
        # cookie 已签发
        assert "copilot_session" in client.cookies

    def test_anonymous_independence(self, client: TestClient) -> None:
        """两次调用应当返回不同的匿名身份。"""
        r1 = client.post("/api/v2/auth/anonymous")
        r2 = client.post("/api/v2/auth/anonymous")
        assert r1.json()["user"]["user_id"] != r2.json()["user"]["user_id"]


class TestWhoAmI:
    def test_anonymous_returns_not_authenticated(self, client: TestClient) -> None:
        resp = client.get("/api/v2/auth/me")
        assert resp.status_code == 200
        assert resp.json() == {"authenticated": False, "user": None}

    def test_after_anonymous_login_returns_user(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, user = authed_client
        # 注：FakeAuth.create_anonymous() 返回的 User 还没 upsert 到 user_repo，
        # 所以 /me 看不到。手动 upsert 一下以走完真实流程。

        app_state = client.app.state  # type: ignore[attr-defined]
        container: AppContainer = app_state.container
        from domain.models import User

        container.user_repo.upsert(
            User(
                user_id=user["user_id"],
                provider=user["provider"],
                provider_id=user["user_id"].split(":", 1)[1],
                email=None,
                display_name=user.get("display_name"),
                avatar_url=user.get("avatar_url"),
                created_at=1.0,
                last_active_at=1.0,
            )
        )
        resp = client.get("/api/v2/auth/me")
        assert resp.status_code == 200
        body = resp.json()
        assert body["authenticated"] is True
        assert body["user"]["user_id"] == user["user_id"]

    def test_with_invalid_cookie_returns_not_authenticated(self, client: TestClient) -> None:
        client.cookies.set("copilot_session", "garbage-token")
        resp = client.get("/api/v2/auth/me")
        assert resp.status_code == 200
        assert resp.json()["authenticated"] is False


class TestLogout:
    def test_clears_cookie(self, authed_client: tuple[TestClient, dict[str, Any]]) -> None:
        client, _ = authed_client
        assert "copilot_session" in client.cookies
        resp = client.post("/api/v2/auth/logout")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        # cookie 已被服务端要求清空（TestClient 解析 Set-Cookie 后会删除）
        assert "copilot_session" not in client.cookies

    def test_records_audit_when_session_active(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        # Step 025e：登录态下 logout 应落 AUTH_LOGOUT 审计
        from domain.models import AuditAction

        client, user = authed_client
        container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
        # 清掉 anonymous_create 的痕迹，只盯 logout
        container.audit_log.entries.clear()  # type: ignore[attr-defined]

        resp = client.post("/api/v2/auth/logout", headers={"X-Request-ID": "req-bye"})
        assert resp.status_code == 200

        entries = container.audit_log.entries  # type: ignore[attr-defined]
        logout_entries = [e for e in entries if e.action == AuditAction.AUTH_LOGOUT]
        assert len(logout_entries) == 1
        e = logout_entries[0]
        assert e.actor_id == user["user_id"]
        assert e.resource == "session"
        assert e.success is True
        assert e.request_id == "req-bye"  # Step 025d contextvar 透传

    def test_logout_without_session_is_silent(self, client: TestClient) -> None:
        # 未登录直接 logout：仍然 200，但不写审计（D2：避免噪音）
        from domain.models import AuditAction

        container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
        before = len(container.audit_log.entries)  # type: ignore[attr-defined]

        resp = client.post("/api/v2/auth/logout")
        assert resp.status_code == 200

        after = len(container.audit_log.entries)  # type: ignore[attr-defined]
        # 0 条 logout 审计写入
        new_entries = container.audit_log.entries[before:]  # type: ignore[attr-defined]
        assert all(e.action != AuditAction.AUTH_LOGOUT for e in new_entries)
        assert after == before  # 整体一条都没多


class TestGithubLogin:
    def test_returns_authorize_url_and_state(self, client: TestClient) -> None:
        resp = client.get("/api/v2/auth/github/login")
        assert resp.status_code == 200
        body = resp.json()
        assert body["authorize_url"].startswith("https://")
        assert body["state"]

    def test_callback_success_sets_cookie(self, client: TestClient) -> None:
        # 先 begin 拿一个合法 state
        begin = client.get("/api/v2/auth/github/login").json()
        state = begin["state"]
        resp = client.get(
            "/api/v2/auth/github/callback",
            params={"code": "fake-code", "state": state},
            follow_redirects=False,
        )
        # 303 重定向回首页，cookie 随 Set-Cookie 下发
        assert resp.status_code == 303, resp.text
        assert resp.headers["location"] == "/"
        assert "copilot_session" in client.cookies

    def test_callback_invalid_state_returns_400(self, client: TestClient) -> None:
        resp = client.get(
            "/api/v2/auth/github/callback",
            params={"code": "fake-code", "state": "never-issued"},
        )
        assert resp.status_code == 400
        assert resp.json()["error_code"] == "OAUTH_EXCHANGE_FAILED"

    def test_callback_missing_params_returns_422(self, client: TestClient) -> None:
        resp = client.get("/api/v2/auth/github/callback")
        assert resp.status_code == 422


class TestRequireOwnerDep:
    """间接验证 require_owner Depends —— 通过 /tasks 路由。"""

    def test_missing_cookie_returns_401(self, client: TestClient) -> None:
        resp = client.get("/api/v2/tasks")
        assert resp.status_code == 401
        body = resp.json()
        assert body["error_code"] == "AUTH_REQUIRED"

    def test_invalid_cookie_returns_401(self, client: TestClient) -> None:
        client.cookies.set("copilot_session", "bogus")
        resp = client.get("/api/v2/tasks")
        assert resp.status_code == 401


# ── Step 012-tail: 管理员权限基线 ─────────────────────────────────────


class TestUserOutIsAdmin:
    """``UserOut.is_admin`` 字段：默认 False；命中 ``admin_user_ids`` 时 True。"""

    def test_default_anonymous_is_not_admin(self, client: TestClient) -> None:
        """默认 admin_user_ids 为空 → 任何用户都不是管理员。"""
        resp = client.post("/api/v2/auth/anonymous")
        assert resp.status_code == 201
        assert resp.json()["user"]["is_admin"] is False

    def test_me_returns_is_admin_false_by_default(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, user = authed_client
        container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
        from domain.models import User as DomainUser

        container.user_repo.upsert(
            DomainUser(
                user_id=user["user_id"],
                provider=user["provider"],
                provider_id=user["user_id"].split(":", 1)[1],
                email=None,
                display_name=user.get("display_name") or "匿名用户",
                avatar_url=None,
                created_at=1.0,
                last_active_at=1.0,
            )
        )
        body = client.get("/api/v2/auth/me").json()
        assert body["user"]["is_admin"] is False


class TestUserOutIsAdminWhenConfigured:
    """配置 admin_user_ids=[github:alice] 后，GitHub 登录用户应为管理员。"""

    @pytest.fixture
    def admin_user_ids(self) -> list[str]:
        return ["github:alice"]  # FakeOAuthProvider 固定返回 github:alice

    def test_github_callback_user_is_admin(self, client: TestClient) -> None:
        # 先 begin 拿合法 state
        state = client.get("/api/v2/auth/github/login").json()["state"]
        # callback 303 重定向回 /；cookie 已下发
        client.get(
            "/api/v2/auth/github/callback",
            params={"code": "fake-code", "state": state},
            follow_redirects=False,
        )
        # FakeAuth.complete_oauth 不会自动 upsert（同匹匿名登录测试的约定），手动补上
        container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
        from domain.models import User as DomainUser

        container.user_repo.upsert(
            DomainUser(
                user_id="github:alice",
                provider="github",
                provider_id="1001",
                email="alice@example.com",
                display_name="Alice",
                avatar_url=None,
                created_at=1.0,
                last_active_at=1.0,
            )
        )
        body = client.get("/api/v2/auth/me").json()
        assert body["authenticated"] is True
        assert body["user"]["user_id"] == "github:alice"
        assert body["user"]["is_admin"] is True


class TestRequireAdminDep:
    """``make_require_admin``：未登录 401 / 非管理员 403 / 管理员 200。"""

    # 挂在 /api/v2 前缀下，走 v2 的 ErrorResponse handler（拍平 detail）
    _ADMIN_PATH = "/api/v2/_test/admin-only"

    @classmethod
    def _mount_admin_route(cls, client: TestClient) -> None:
        container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
        admin_dep = make_require_admin(container)
        router = APIRouter()

        @router.get("/api/v2/_test/admin-only")
        def admin_only(uid: str = Depends(admin_dep)) -> dict[str, str]:
            return {"uid": uid}

        client.app.include_router(router)  # type: ignore[attr-defined]

    def test_missing_cookie_returns_401(self, client: TestClient) -> None:
        self._mount_admin_route(client)
        resp = client.get(self._ADMIN_PATH)
        assert resp.status_code == 401
        assert resp.json()["error_code"] == "AUTH_REQUIRED"

    def test_logged_in_but_non_admin_returns_403(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        self._mount_admin_route(client)
        resp = client.get(self._ADMIN_PATH)
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "ADMIN_REQUIRED"


class TestRequireAdminDepAsAdmin:
    """admin_user_ids 含当前用户时，require_admin 放行。"""

    @pytest.fixture
    def admin_user_ids(self) -> list[str]:
        return ["github:alice"]

    def test_admin_request_returns_200(self, client: TestClient) -> None:
        # 完成 GitHub 登录 → 拿到 github:alice cookie
        state = client.get("/api/v2/auth/github/login").json()["state"]
        client.get(
            "/api/v2/auth/github/callback",
            params={"code": "fake-code", "state": state},
            follow_redirects=False,
        )
        # 挂临时管理员路由（同样走 /api/v2 前缀以享受 v2 error handler）
        container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
        admin_dep = make_require_admin(container)
        router = APIRouter()

        @router.get("/api/v2/_test/admin-only")
        def admin_only(uid: str = Depends(admin_dep)) -> dict[str, str]:
            return {"uid": uid}

        client.app.include_router(router)  # type: ignore[attr-defined]
        resp = client.get("/api/v2/_test/admin-only")
        assert resp.status_code == 200
        assert resp.json() == {"uid": "github:alice"}
