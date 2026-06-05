"""AuthService：组合 OAuth provider + JWT issuer + AnonymousProvider，实现 AuthPort。

职责：
- `begin_oauth`: 委托对应 provider 生成 (auth_url, state)，并把 state 暂存（10 分钟 TTL）
- `complete_oauth`: 校验 state（防 CSRF）→ 委托 provider 换 user → upsert → 返回
- `issue_jwt` / `verify_jwt`: 委托 JwtIssuer
- `create_anonymous`: 调 AnonymousProvider → upsert → 返回

匿名 → 登录的资源迁移（merge_owner）由上层 use-case 显式调用 `UserRepoPort.merge_owner`，
不在 AuthPort 接口内（保持 AuthPort 单一职责，与 §4.2 spec 对齐）。
"""

from __future__ import annotations

import time
from typing import Any, Protocol

from domain.errors import InvalidToken, OAuthFlowError
from domain.models import User
from infra.auth.anonymous import AnonymousProvider
from infra.auth.jwt_issuer import JwtIssuer

DEFAULT_STATE_TTL_SECONDS = 600  # 10 分钟


class _OAuthProviderLike(Protocol):
    """`begin() / exchange(code, state)` 兼容鸭子接口。

    `GitHubOAuthProvider` 与测试 `FakeGitHubOAuth` 都满足此协议。
    """

    def begin(self) -> tuple[str, str]: ...

    def exchange(self, code: str, state: str) -> User: ...


class _UserRepoLike(Protocol):
    def upsert(self, user: User) -> None: ...

    def get(self, user_id: str) -> User | None: ...

    def touch(self, user_id: str) -> None: ...


class AuthService:
    """`AuthPort` 的统一实现。"""

    def __init__(
        self,
        *,
        providers: dict[str, _OAuthProviderLike],
        jwt_issuer: JwtIssuer,
        user_repo: _UserRepoLike,
        anonymous: AnonymousProvider | None = None,
        state_ttl_seconds: int = DEFAULT_STATE_TTL_SECONDS,
        clock: Any = time.time,
    ) -> None:
        if not providers:
            msg = "AuthService 至少需要一个 provider"
            raise ValueError(msg)
        self._providers = dict(providers)
        self._jwt = jwt_issuer
        self._user_repo = user_repo
        self._anonymous = anonymous or AnonymousProvider(clock=clock)
        self._state_ttl = state_ttl_seconds
        self._clock = clock
        # 内存级 state 缓存：state → (provider, issued_at)
        self._states: dict[str, tuple[str, float]] = {}

    # ── OAuth ─────────────────────────────────────────────────────────

    def begin_oauth(self, provider: str) -> tuple[str, str]:
        prov = self._providers.get(provider)
        if prov is None or provider == "anonymous":
            raise OAuthFlowError(f"unknown OAuth provider: {provider!r}")
        auth_url, state = prov.begin()
        self._gc_states()
        self._states[state] = (provider, float(self._clock()))
        return auth_url, state

    def complete_oauth(self, provider: str, code: str, state: str) -> User:
        prov = self._providers.get(provider)
        if prov is None or provider == "anonymous":
            raise OAuthFlowError(f"unknown OAuth provider: {provider!r}")
        self._consume_state(provider, state)
        user = prov.exchange(code, state)
        existing = self._user_repo.get(user.user_id)
        if existing is None:
            self._user_repo.upsert(user)
        else:
            # 已存在用户：保留 created_at，刷新其它字段
            refreshed = user.model_copy(update={"created_at": existing.created_at})
            self._user_repo.upsert(refreshed)
        return user if existing is None else self._user_repo.get(user.user_id) or user

    # ── JWT ──────────────────────────────────────────────────────────

    def issue_jwt(self, user_id: str) -> str:
        if not user_id:
            raise InvalidToken("issue_jwt: empty user_id")
        return self._jwt.issue(user_id)

    def verify_jwt(self, token: str) -> str | None:
        return self._jwt.verify(token)

    # ── Anonymous ────────────────────────────────────────────────────

    def create_anonymous(self) -> User:
        user = self._anonymous.create()
        self._user_repo.upsert(user)
        return user

    # ── 内部：state 生命周期 ────────────────────────────────────────

    def _consume_state(self, provider: str, state: str) -> None:
        if not state:
            raise OAuthFlowError("missing state")
        record = self._states.pop(state, None)
        if record is None:
            raise OAuthFlowError("unknown or already-consumed state")
        recorded_provider, issued_at = record
        if recorded_provider != provider:
            raise OAuthFlowError(
                f"state provider mismatch: expected {recorded_provider!r}, got {provider!r}"
            )
        if float(self._clock()) - issued_at > self._state_ttl:
            raise OAuthFlowError("state expired")

    def _gc_states(self) -> None:
        """惰性清理过期 state。"""
        now = float(self._clock())
        expired = [
            s for s, (_, ts) in self._states.items() if now - ts > self._state_ttl
        ]
        for s in expired:
            self._states.pop(s, None)
