"""FakeAuth + FakeOAuthProvider 自身契约 + 行为测试。

确保 Fake 不会与 AuthPort 漂移；AuthService 加方法时此处会立刻报错。
"""

from __future__ import annotations

import pytest

from domain.errors import OAuthFlowError
from domain.ports import AuthPort
from tests.fakes.fake_auth import FakeAuth, FakeOAuthProvider


class TestFakeAuthConformance:
    def test_fake_auth_is_auth_port(self) -> None:
        assert isinstance(FakeAuth(), AuthPort)


class TestFakeAuthBehavior:
    def test_begin_then_complete(self) -> None:
        auth = FakeAuth()
        url, state = auth.begin_oauth("github")
        assert state in url
        u = auth.complete_oauth("github", code="abc", state=state)
        assert u.user_id == "github:alice"
        assert auth.complete_calls == [("github", "abc", state)]

    def test_unknown_provider_in_begin(self) -> None:
        with pytest.raises(OAuthFlowError):
            FakeAuth().begin_oauth("google")

    def test_state_replay_rejected(self) -> None:
        auth = FakeAuth()
        _, state = auth.begin_oauth("github")
        auth.complete_oauth("github", code="c", state=state)
        with pytest.raises(OAuthFlowError):
            auth.complete_oauth("github", code="c", state=state)

    def test_jwt_round_trip(self) -> None:
        auth = FakeAuth()
        t = auth.issue_jwt("github:alice")
        assert auth.verify_jwt(t) == "github:alice"
        assert auth.verify_jwt("never-issued") is None

    def test_create_anonymous(self) -> None:
        auth = FakeAuth()
        u = auth.create_anonymous()
        assert u.user_id.startswith("anon:")
        assert auth.anonymous_calls == 1


class TestFakeOAuthProvider:
    def test_default_returns_alice(self) -> None:
        p = FakeOAuthProvider()
        url, state = p.begin()
        assert state in url
        u = p.exchange(code="c", state=state)
        assert u.user_id == "github:alice"
        assert u.provider == "github"
        assert p.exchange_calls == [("c", state)]

    def test_persona_override(self) -> None:
        p = FakeOAuthProvider(login="bob", provider_id="999", display_name="Bob")
        u = p.exchange(code="c", state="s")
        assert u.user_id == "github:bob"
        assert u.provider_id == "999"
        assert u.display_name == "Bob"

    def test_raise_on_exchange(self) -> None:
        p = FakeOAuthProvider(raise_on_exchange=OAuthFlowError("forced"))
        with pytest.raises(OAuthFlowError):
            p.exchange(code="c", state="s")
