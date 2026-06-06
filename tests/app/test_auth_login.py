"""AuthLoginUseCase 单测：依赖 FakeAuth，不真碰 JWT/HTTP。"""

from __future__ import annotations

import pytest

from app.use_cases.auth_login import AuthLoginUseCase
from domain.errors import InvalidToken, OAuthFlowError
from domain.models import AuditAction
from tests.fakes import FakeAuditLogRepo, FakeAuth


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


# ─────────────────────────── Step 025c：审计 hook ────────────────────────


def _make_uc_with_audit() -> tuple[AuthLoginUseCase, FakeAuth, FakeAuditLogRepo]:
    auth = FakeAuth()
    audit = FakeAuditLogRepo()
    uc = AuthLoginUseCase(auth, audit_log=audit)
    return uc, auth, audit


class TestAuditHooks:
    def test_audit_log_none_bypasses_hook(self) -> None:
        # 旧调用方式（无 audit_log）必须依旧工作，不能崩
        uc = AuthLoginUseCase(FakeAuth())
        _, state = uc.begin("github")
        user, token = uc.complete("github", code="abc", state=state)
        assert user.user_id == "github:alice"
        assert token

    def test_complete_success_records_audit(self) -> None:
        uc, _auth, audit = _make_uc_with_audit()
        _, state = uc.begin("github")
        assert audit.entries == []  # begin 不应落审计

        user, _token = uc.complete("github", code="abc", state=state)

        assert len(audit.entries) == 1
        e = audit.entries[0]
        assert e.action == AuditAction.AUTH_LOGIN_SUCCESS
        assert e.actor_id == user.user_id == "github:alice"
        assert e.resource == "oauth:github"
        assert e.success is True
        assert e.error is None
        assert e.extra_json == {"provider": "github"}

    def test_complete_failure_records_audit(self) -> None:
        uc, _auth, audit = _make_uc_with_audit()
        with pytest.raises(OAuthFlowError):
            uc.complete("github", code="abc", state="never_issued")

        assert len(audit.entries) == 1
        e = audit.entries[0]
        assert e.action == AuditAction.AUTH_LOGIN_FAILURE
        assert e.actor_id == "system:unknown"
        assert e.resource == "oauth:github"
        assert e.success is False
        assert e.error  # 非空 error 字符串
        assert e.extra_json["provider"] == "github"
        assert "reason" in e.extra_json

    def test_login_anonymous_records_audit(self) -> None:
        uc, _auth, audit = _make_uc_with_audit()
        user, _token = uc.login_anonymous()

        assert len(audit.entries) == 1
        e = audit.entries[0]
        assert e.action == AuditAction.AUTH_ANONYMOUS_CREATE
        assert e.actor_id == user.user_id
        assert e.actor_id.startswith("anon:")
        assert e.resource == "anonymous"
        assert e.success is True
        assert e.extra_json == {"provider": "anonymous"}

    def test_request_id_propagates_when_provided(self) -> None:
        uc, _auth, audit = _make_uc_with_audit()
        _, state = uc.begin("github")
        uc.complete("github", code="abc", state=state, request_id="req-xyz")
        uc.login_anonymous(request_id="req-anon")

        assert [e.request_id for e in audit.entries] == ["req-xyz", "req-anon"]

    def test_identify_and_require_do_not_audit(self) -> None:
        uc, _auth, audit = _make_uc_with_audit()
        _, token = uc.login_anonymous()
        audit.entries.clear()  # 清掉 anonymous 创建的审计

        uc.identify(token)
        uc.identify(None)
        uc.require(token)
        with pytest.raises(InvalidToken):
            uc.require(None)

        assert audit.entries == []  # 只读路径不应落审计


# ─────────────────────────── Step 025e：登出 ────────────────────────────


class TestLogout:
    def _uc(self) -> tuple[AuthLoginUseCase, FakeAuth, FakeAuditLogRepo]:
        auth = FakeAuth()
        audit = FakeAuditLogRepo()
        uc = AuthLoginUseCase(auth, audit_log=audit)
        return uc, auth, audit

    def test_logout_with_valid_token_records_audit(self) -> None:
        uc, _auth, audit = self._uc()
        _, token = uc.login_anonymous()
        audit.entries.clear()  # 清掉 anonymous_create 这条

        result = uc.logout(token)

        assert result is not None and result.startswith("anon:")
        assert len(audit.entries) == 1
        e = audit.entries[0]
        assert e.action == AuditAction.AUTH_LOGOUT
        assert e.actor_id == result
        assert e.resource == "session"
        assert e.success is True
        assert e.error is None
        assert e.extra_json == {}

    def test_logout_none_token_is_noop(self) -> None:
        uc, _auth, audit = self._uc()
        result = uc.logout(None)
        assert result is None
        assert audit.entries == []  # 未登录 logout 不落审计

    def test_logout_empty_token_is_noop(self) -> None:
        uc, _auth, audit = self._uc()
        result = uc.logout("")
        assert result is None
        assert audit.entries == []

    def test_logout_invalid_token_is_noop(self) -> None:
        uc, _auth, audit = self._uc()
        result = uc.logout("garbage-token")
        assert result is None
        assert audit.entries == []

    def test_logout_audit_log_none_safe(self) -> None:
        # 旧 audit_log=None 调用方式仍兼容
        uc = AuthLoginUseCase(FakeAuth())  # audit_log 默认 None
        _, token = uc.login_anonymous()
        assert uc.logout(token) is not None  # 不抛

    def test_logout_request_id_propagates(self) -> None:
        uc, _auth, audit = self._uc()
        _, token = uc.login_anonymous()
        audit.entries.clear()

        uc.logout(token, request_id="req-logout-1")
        assert len(audit.entries) == 1
        assert audit.entries[0].request_id == "req-logout-1"

    def test_logout_after_github_login(self) -> None:
        uc, _auth, audit = self._uc()
        _, state = uc.begin("github")
        _user, token = uc.complete("github", code="abc", state=state)
        audit.entries.clear()

        result = uc.logout(token)
        assert result == "github:alice"
        assert audit.entries[0].action == AuditAction.AUTH_LOGOUT
        assert audit.entries[0].actor_id == "github:alice"
