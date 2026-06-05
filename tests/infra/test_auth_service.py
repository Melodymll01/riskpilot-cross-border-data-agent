"""AuthService 端到端测试：state 生命周期 + JWT + anonymous + provider 路由。

用 `FakeOAuthProvider` 替代真实 GitHub HTTP（其本身已经在 test_github_oauth.py 测过）。
"""

from __future__ import annotations

import pytest

from domain.errors import OAuthFlowError
from domain.ports import AuthPort
from infra.auth import AuthService, JwtIssuer
from infra.auth.anonymous import AnonymousProvider
from tests.fakes.fake_auth import FakeOAuthProvider
from tests.fakes.fake_repos import InMemoryUserRepo


class _Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def jwt_issuer(clock: _Clock) -> JwtIssuer:
    return JwtIssuer(secret="ci_test_secret_abcdef", ttl_seconds=3600, clock=clock)


@pytest.fixture
def user_repo() -> InMemoryUserRepo:
    return InMemoryUserRepo()


@pytest.fixture
def gh_provider(clock: _Clock) -> FakeOAuthProvider:
    return FakeOAuthProvider(login="alice", provider_id="1001", clock=clock)


@pytest.fixture
def auth(
    gh_provider: FakeOAuthProvider,
    jwt_issuer: JwtIssuer,
    user_repo: InMemoryUserRepo,
    clock: _Clock,
) -> AuthService:
    return AuthService(
        providers={"github": gh_provider},
        jwt_issuer=jwt_issuer,
        user_repo=user_repo,
        anonymous=AnonymousProvider(clock=clock),
        state_ttl_seconds=600,
        clock=clock,
    )


# ── 契约 ───────────────────────────────────────────────────────────────


class TestProtocolConformance:
    def test_implements_auth_port(self, auth: AuthService) -> None:
        assert isinstance(auth, AuthPort)


# ── begin / complete 主流程 ────────────────────────────────────────────


class TestOAuthFlow:
    def test_begin_returns_url_and_state(
        self, auth: AuthService, gh_provider: FakeOAuthProvider
    ) -> None:
        url, state = auth.begin_oauth("github")
        assert "fake.oauth" in url
        assert state in url
        assert gh_provider.begin_calls == 1

    def test_complete_happy_path(
        self,
        auth: AuthService,
        user_repo: InMemoryUserRepo,
    ) -> None:
        _, state = auth.begin_oauth("github")
        user = auth.complete_oauth("github", code="abc", state=state)
        assert user.user_id == "github:alice"
        assert user_repo.get("github:alice") is not None

    def test_unknown_provider_in_begin(self, auth: AuthService) -> None:
        with pytest.raises(OAuthFlowError):
            auth.begin_oauth("google")

    def test_unknown_provider_in_complete(self, auth: AuthService) -> None:
        _, state = auth.begin_oauth("github")
        with pytest.raises(OAuthFlowError):
            auth.complete_oauth("google", code="c", state=state)

    def test_state_replay_rejected(self, auth: AuthService) -> None:
        _, state = auth.begin_oauth("github")
        auth.complete_oauth("github", code="c1", state=state)
        with pytest.raises(OAuthFlowError):
            auth.complete_oauth("github", code="c2", state=state)

    def test_unknown_state_rejected(self, auth: AuthService) -> None:
        with pytest.raises(OAuthFlowError):
            auth.complete_oauth("github", code="c", state="never_issued")

    def test_empty_state_rejected(self, auth: AuthService) -> None:
        with pytest.raises(OAuthFlowError):
            auth.complete_oauth("github", code="c", state="")

    def test_state_provider_mismatch(
        self,
        gh_provider: FakeOAuthProvider,
        jwt_issuer: JwtIssuer,
        user_repo: InMemoryUserRepo,
        clock: _Clock,
    ) -> None:
        # 双 provider 场景：state 用 google 颁发，但 complete 走 github
        google_provider = FakeOAuthProvider(login="bob", provider_id="9", clock=clock)
        auth = AuthService(
            providers={"github": gh_provider, "google": google_provider},
            jwt_issuer=jwt_issuer,
            user_repo=user_repo,
            clock=clock,
        )
        _, state = auth.begin_oauth("google")
        with pytest.raises(OAuthFlowError):
            auth.complete_oauth("github", code="c", state=state)

    def test_state_expired(
        self,
        auth: AuthService,
        clock: _Clock,
    ) -> None:
        _, state = auth.begin_oauth("github")
        clock.now += 10_000  # 远超 600s TTL
        with pytest.raises(OAuthFlowError):
            auth.complete_oauth("github", code="c", state=state)

    def test_existing_user_preserves_created_at(
        self,
        auth: AuthService,
        user_repo: InMemoryUserRepo,
        clock: _Clock,
    ) -> None:
        # 第一次登录
        _, s1 = auth.begin_oauth("github")
        u1 = auth.complete_oauth("github", code="c", state=s1)
        original_created = u1.created_at

        # 模拟过一阵后第二次登录
        clock.now += 100
        _, s2 = auth.begin_oauth("github")
        auth.complete_oauth("github", code="c2", state=s2)
        stored = user_repo.get("github:alice")
        assert stored is not None
        assert stored.created_at == original_created  # 保留旧的 created_at


# ── JWT ────────────────────────────────────────────────────────────────


class TestJwt:
    def test_issue_then_verify(self, auth: AuthService) -> None:
        token = auth.issue_jwt("github:alice")
        assert auth.verify_jwt(token) == "github:alice"

    def test_verify_garbage(self, auth: AuthService) -> None:
        assert auth.verify_jwt("invalid") is None

    def test_verify_empty(self, auth: AuthService) -> None:
        assert auth.verify_jwt("") is None


# ── Anonymous ──────────────────────────────────────────────────────────


class TestAnonymous:
    def test_create_anonymous_persists(
        self, auth: AuthService, user_repo: InMemoryUserRepo
    ) -> None:
        u = auth.create_anonymous()
        assert u.user_id.startswith("anon:")
        assert user_repo.get(u.user_id) is not None
