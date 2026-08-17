"""GitHub OAuth 2.0 Provider。

实现 OAuth Authorization Code 流程：
1. `begin()` → `(auth_url, state)`，前端重定向到 auth_url
2. GitHub 回调时携带 `code` 与 `state`
3. `exchange(code, state)` → POST access_token → GET /user → 构造 User

注入策略：
- 默认用 `requests` 库；测试可通过 `responses.activate` 拦截 HTTP，
  或注入自定义 session（任何 `.post()` / `.get()` 兼容 requests.Session 的对象）。
- `state` 不在本 Provider 内部校验；由 `AuthService` 统一管 state 生命周期。
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode

from domain.errors import OAuthFlowError
from domain.models import User

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"

DEFAULT_TIMEOUT = 10


class GitHubOAuthProvider:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        *,
        scope: str = "read:user user:email",
        session: Any = None,  # requests.Session-like
        timeout: float = DEFAULT_TIMEOUT,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not client_id or not client_secret:
            msg = "GitHub client_id / client_secret 必填"
            raise ValueError(msg)
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._scope = scope
        self._timeout = timeout
        self._clock = clock
        if session is None:
            import requests

            session = requests.Session()
        self._session = session

    # ── Step 1: 生成 authorize URL ────────────────────────────────────

    def begin(self) -> tuple[str, str]:
        state = secrets.token_urlsafe(24)
        params = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "scope": self._scope,
            "state": state,
            "allow_signup": "true",
        }
        return f"{GITHUB_AUTHORIZE_URL}?{urlencode(params)}", state

    # ── Step 2 + 3: code → token → user ─────────────────────────────

    def exchange(self, code: str, state: str) -> User:
        if not code:
            raise OAuthFlowError("missing OAuth `code`")
        token = self._exchange_token(code, state)
        gh_user = self._fetch_user(token)
        return self._to_domain_user(gh_user)

    def _exchange_token(self, code: str, state: str) -> str:
        try:
            resp = self._session.post(
                GITHUB_TOKEN_URL,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "code": code,
                    "redirect_uri": self._redirect_uri,
                    "state": state,
                },
                headers={"Accept": "application/json"},
                timeout=self._timeout,
            )
        except Exception as e:  # noqa: BLE001  (网络错误统一翻译)
            raise OAuthFlowError(f"token exchange HTTP error: {e}") from e

        if not (200 <= resp.status_code < 300):
            raise OAuthFlowError(f"token exchange returned {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            raise OAuthFlowError("token endpoint returned non-JSON body") from e

        token = data.get("access_token")
        if not token:
            error = data.get("error", "unknown")
            raise OAuthFlowError(f"GitHub token error: {error}")
        return str(token)

    def _fetch_user(self, token: str) -> dict[str, Any]:
        try:
            resp = self._session.get(
                GITHUB_USER_URL,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                timeout=self._timeout,
            )
        except Exception as e:  # noqa: BLE001
            raise OAuthFlowError(f"user fetch HTTP error: {e}") from e

        if not (200 <= resp.status_code < 300):
            raise OAuthFlowError(f"GET /user returned {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            raise OAuthFlowError("user endpoint returned non-JSON body") from e
        if not isinstance(data, dict):
            raise OAuthFlowError("user endpoint returned non-object body")
        return data

    def _to_domain_user(self, gh_user: dict[str, Any]) -> User:
        login = gh_user.get("login")
        if not isinstance(login, str) or not login:
            raise OAuthFlowError("GitHub user payload missing `login`")
        gh_id = gh_user.get("id")
        if gh_id is None:
            raise OAuthFlowError("GitHub user payload missing `id`")
        now = self._clock()
        return User(
            user_id=f"github:{login}",
            provider="github",
            provider_id=str(gh_id),
            email=gh_user.get("email") or None,
            display_name=(gh_user.get("name") or login),
            avatar_url=gh_user.get("avatar_url") or None,
            created_at=now,
            last_active_at=now,
        )
