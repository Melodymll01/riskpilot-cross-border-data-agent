"""测试用 OAuth Provider Fake：免网络。

行为：
- `begin()` 返回 `(fake_url, secrets.token_urlsafe(...))`
- `exchange(code, state)` 直接返回预设 persona User，便于 AuthService 集成测试。
"""

from __future__ import annotations

import secrets
import time

from domain.errors import OAuthFlowError
from domain.models import User


class FakeOAuthProvider:
    """可预设 persona / 抛错 / 自定义 user 的 Fake OAuth Provider。"""

    def __init__(
        self,
        login: str = "alice",
        provider_id: str = "12345",
        display_name: str = "Alice",
        email: str | None = "alice@example.com",
        avatar_url: str | None = None,
        *,
        raise_on_exchange: Exception | None = None,
        clock: callable = time.time,  # type: ignore[type-arg]
    ) -> None:
        self.login = login
        self.provider_id = provider_id
        self.display_name = display_name
        self.email = email
        self.avatar_url = avatar_url
        self.raise_on_exchange = raise_on_exchange
        self._clock = clock
        self.begin_calls = 0
        self.exchange_calls: list[tuple[str, str]] = []

    def begin(self) -> tuple[str, str]:
        self.begin_calls += 1
        state = secrets.token_urlsafe(16)
        return f"https://fake.oauth/authorize?state={state}", state

    def exchange(self, code: str, state: str) -> User:
        self.exchange_calls.append((code, state))
        if self.raise_on_exchange is not None:
            raise self.raise_on_exchange
        if not code:
            raise OAuthFlowError("FakeOAuthProvider: missing code")
        now = self._clock()
        return User(
            user_id=f"github:{self.login}",
            provider="github",
            provider_id=self.provider_id,
            email=self.email,
            display_name=self.display_name,
            avatar_url=self.avatar_url,
            created_at=now,
            last_active_at=now,
        )


# ── FakeAuth：直接实现 AuthPort，供上层（app/Agent）单测用 ─────────────


class FakeAuth:
    """`AuthPort` 内存 Fake：不走 JWT 库与 HTTP，所有动作都可追踪、可预设。

    适用场景：app 层 / api 层用例测试，不需要测真实 JWT 编解码或 OAuth 网络流。
    单测 `infra/auth/*` 自身请用真实 `AuthService` + `FakeOAuthProvider`，本类不参与。
    """

    def __init__(
        self,
        users_by_provider: dict[str, User] | None = None,
        token_prefix: str = "fake-jwt-",
    ) -> None:
        if users_by_provider is None:
            now = time.time()
            users_by_provider = {
                "github": User(
                    user_id="github:alice",
                    provider="github",
                    provider_id="1001",
                    email="alice@example.com",
                    display_name="Alice",
                    avatar_url=None,
                    created_at=now,
                    last_active_at=now,
                ),
            }
        self._users_by_provider = dict(users_by_provider)
        self._token_prefix = token_prefix
        self._states: set[str] = set()
        self._tokens: dict[str, str] = {}
        # 调用追踪
        self.begin_calls: list[str] = []
        self.complete_calls: list[tuple[str, str, str]] = []
        self.anonymous_calls = 0

    def begin_oauth(self, provider: str) -> tuple[str, str]:
        if provider not in self._users_by_provider:
            raise OAuthFlowError(f"FakeAuth: provider {provider!r} 未配置")
        self.begin_calls.append(provider)
        state = secrets.token_urlsafe(8)
        self._states.add(state)
        return f"https://fake.local/{provider}/authorize?state={state}", state

    def complete_oauth(self, provider: str, code: str, state: str) -> User:
        self.complete_calls.append((provider, code, state))
        if provider not in self._users_by_provider:
            raise OAuthFlowError(f"FakeAuth: provider {provider!r} 未配置")
        if state not in self._states:
            raise OAuthFlowError("FakeAuth: state 无效或已使用")
        if not code:
            raise OAuthFlowError("FakeAuth: code 不能为空")
        self._states.discard(state)
        return self._users_by_provider[provider]

    def issue_jwt(self, user_id: str) -> str:
        token = f"{self._token_prefix}{user_id}"
        self._tokens[token] = user_id
        return token

    def verify_jwt(self, token: str) -> str | None:
        return self._tokens.get(token)

    def create_anonymous(self) -> User:
        self.anonymous_calls += 1
        ts = time.time()
        uid = secrets.token_hex(8)
        return User(
            user_id=f"anon:{uid}",
            provider="anonymous",
            provider_id=uid,
            email=None,
            display_name="匿名用户",
            avatar_url=None,
            created_at=ts,
            last_active_at=ts,
        )
