"""AuthLoginUseCase 单测：依赖 FakeAuth，不真碰 JWT/HTTP。"""

from __future__ import annotations

import pytest

from app.use_cases.auth_login import AuthLoginUseCase
from domain.errors import InvalidToken, OAuthFlowError
from tests.fakes import FakeAuth


class TestBeginComplete:
    def test_begin_returns_url_and_state(self) -> None:
        uc = AuthLoginUseCase(FakeAuth())
        url, state = uc.begin("github")
        assert state and state in url

    def test_begin_unknown_provider(self) -> None:
        uc = AuthLoginUseCase(FakeAuth())
        with pytest.raises(OAuthFlowError):
            uc.begin("google")

    def test_complete_returns_user_and_token(self) -> None:
        auth = FakeAuth()
        uc = AuthLoginUseCase(auth)
        url, state = uc.begin("github")
        user, token = uc.complete("github", code="abc", state=state)
        assert user.user_id == "github:alice"
        assert token.startswith("fake-jwt-")
        # 同一个 token 能反查到 user_id
        assert auth.verify_jwt(token) == "github:alice"

    def test_complete_invalid_state(self) -> None:
        uc = AuthLoginUseCase(FakeAuth())
        with pytest.raises(OAuthFlowError):
            uc.complete("github", code="abc", state="never_issued")


class TestAnonymous:
    def test_login_anonymous_returns_user_and_token(self) -> None:
        auth = FakeAuth()
        uc = AuthLoginUseCase(auth)
        user, token = uc.login_anonymous()
        assert user.user_id.startswith("anon:")
        assert auth.verify_jwt(token) == user.user_id


class TestIdentifyAndRequire:
    def test_identify_valid(self) -> None:
        auth = FakeAuth()
        uc = AuthLoginUseCase(auth)
        _, token = uc.login_anonymous()
        uid = uc.identify(token)
        assert uid and uid.startswith("anon:")

    def test_identify_none_or_empty(self) -> None:
        uc = AuthLoginUseCase(FakeAuth())
        assert uc.identify(None) is None
        assert uc.identify("") is None

    def test_identify_invalid(self) -> None:
        uc = AuthLoginUseCase(FakeAuth())
        assert uc.identify("not-a-real-token") is None

    def test_require_raises_on_invalid(self) -> None:
        uc = AuthLoginUseCase(FakeAuth())
        with pytest.raises(InvalidToken):
            uc.require(None)
        with pytest.raises(InvalidToken):
            uc.require("garbage")

    def test_require_returns_user_id_on_valid(self) -> None:
        auth = FakeAuth()
        uc = AuthLoginUseCase(auth)
        _, token = uc.login_anonymous()
        assert uc.require(token).startswith("anon:")
