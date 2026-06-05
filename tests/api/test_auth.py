"""``/api/v2/auth/*`` 路由测试。"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient


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
        from app.container import AppContainer

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

    def test_with_invalid_cookie_returns_not_authenticated(
        self, client: TestClient
    ) -> None:
        client.cookies.set("copilot_session", "garbage-token")
        resp = client.get("/api/v2/auth/me")
        assert resp.status_code == 200
        assert resp.json()["authenticated"] is False


class TestLogout:
    def test_clears_cookie(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        assert "copilot_session" in client.cookies
        resp = client.post("/api/v2/auth/logout")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        # cookie 已被服务端要求清空（TestClient 解析 Set-Cookie 后会删除）
        assert "copilot_session" not in client.cookies


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
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["user"]["user_id"].startswith("github:")
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
