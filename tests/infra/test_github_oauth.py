"""GitHubOAuthProvider 单测：用 `responses` 拦截真实 HTTP。"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
import responses

from domain.errors import OAuthFlowError
from domain.models import User
from infra.auth.github_oauth import GitHubOAuthProvider


@pytest.fixture
def provider() -> GitHubOAuthProvider:
    return GitHubOAuthProvider(
        client_id="cli_id_xx",
        client_secret="cli_secret_xx",
        redirect_uri="https://app.example.com/auth/github/callback",
    )


# ── begin() ────────────────────────────────────────────────────────────


class TestBegin:
    def test_url_components(self, provider: GitHubOAuthProvider) -> None:
        url, state = provider.begin()
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        assert parsed.netloc == "github.com"
        assert parsed.path == "/login/oauth/authorize"
        assert qs["client_id"] == ["cli_id_xx"]
        assert qs["redirect_uri"] == ["https://app.example.com/auth/github/callback"]
        assert qs["state"] == [state]
        assert "read:user" in qs["scope"][0]

    def test_state_uniqueness(self, provider: GitHubOAuthProvider) -> None:
        states = {provider.begin()[1] for _ in range(10)}
        assert len(states) == 10


# ── exchange() 成功路径 ────────────────────────────────────────────────


class TestExchangeSuccess:
    @responses.activate
    def test_happy_path(self, provider: GitHubOAuthProvider) -> None:
        responses.post(
            "https://github.com/login/oauth/access_token",
            json={"access_token": "ghu_xxx", "token_type": "bearer", "scope": "read:user"},
            status=200,
        )
        responses.get(
            "https://api.github.com/user",
            json={
                "login": "alice",
                "id": 12345,
                "name": "Alice",
                "email": "alice@example.com",
                "avatar_url": "https://avatars.example.com/alice.png",
            },
            status=200,
        )
        user = provider.exchange(code="abc123", state="state_xxx")
        assert isinstance(user, User)
        assert user.user_id == "github:alice"
        assert user.provider == "github"
        assert user.provider_id == "12345"
        assert user.email == "alice@example.com"
        assert user.display_name == "Alice"

    @responses.activate
    def test_name_falls_back_to_login(self, provider: GitHubOAuthProvider) -> None:
        responses.post(
            "https://github.com/login/oauth/access_token",
            json={"access_token": "ghu_xxx"},
            status=200,
        )
        responses.get(
            "https://api.github.com/user",
            json={"login": "bob", "id": 999, "name": None, "avatar_url": None},
            status=200,
        )
        user = provider.exchange(code="c", state="s")
        assert user.display_name == "bob"
        assert user.email is None
        assert user.avatar_url is None


# ── exchange() 失败路径 ────────────────────────────────────────────────


class TestExchangeErrors:
    def test_empty_code(self, provider: GitHubOAuthProvider) -> None:
        with pytest.raises(OAuthFlowError):
            provider.exchange(code="", state="s")

    @responses.activate
    def test_token_endpoint_5xx(self, provider: GitHubOAuthProvider) -> None:
        responses.post(
            "https://github.com/login/oauth/access_token",
            body="oops",
            status=500,
        )
        with pytest.raises(OAuthFlowError):
            provider.exchange(code="c", state="s")

    @responses.activate
    def test_token_endpoint_returns_error(self, provider: GitHubOAuthProvider) -> None:
        responses.post(
            "https://github.com/login/oauth/access_token",
            json={"error": "bad_verification_code"},
            status=200,
        )
        with pytest.raises(OAuthFlowError):
            provider.exchange(code="c", state="s")

    @responses.activate
    def test_user_endpoint_4xx(self, provider: GitHubOAuthProvider) -> None:
        responses.post(
            "https://github.com/login/oauth/access_token",
            json={"access_token": "tok"},
            status=200,
        )
        responses.get(
            "https://api.github.com/user",
            json={"message": "Bad credentials"},
            status=401,
        )
        with pytest.raises(OAuthFlowError):
            provider.exchange(code="c", state="s")

    @responses.activate
    def test_user_payload_missing_login(self, provider: GitHubOAuthProvider) -> None:
        responses.post(
            "https://github.com/login/oauth/access_token",
            json={"access_token": "tok"},
            status=200,
        )
        responses.get(
            "https://api.github.com/user",
            json={"id": 1, "name": "no login here"},
            status=200,
        )
        with pytest.raises(OAuthFlowError):
            provider.exchange(code="c", state="s")
