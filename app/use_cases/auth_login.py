"""AuthLoginUseCase：把 ``AuthPort`` 的 OAuth/anonymous + JWT 流程串成单一接口。

API 层只看到这三个动作：
- ``begin(provider)`` → 给前端 (auth_url, state)
- ``complete(provider, code, state)`` → (User, JWT)
- ``login_anonymous()`` → (User, JWT)
- ``identify(token)`` → user_id | None

不在本 use case 里做 cookie / session 处理；那些属于 api 层。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain.errors import InvalidToken

if TYPE_CHECKING:
    from domain.models import User
    from domain.ports import AuthPort


class AuthLoginUseCase:
    def __init__(self, auth: AuthPort) -> None:
        self._auth = auth

    def begin(self, provider: str) -> tuple[str, str]:
        """委托 AuthPort.begin_oauth；任何 provider 错误透传 OAuthFlowError。"""
        return self._auth.begin_oauth(provider)

    def complete(self, provider: str, code: str, state: str) -> tuple[User, str]:
        """完成 OAuth 回调，颁发 JWT。"""
        user = self._auth.complete_oauth(provider, code, state)
        token = self._auth.issue_jwt(user.user_id)
        return user, token

    def login_anonymous(self) -> tuple[User, str]:
        """创建匿名用户并颁发 JWT。"""
        user = self._auth.create_anonymous()
        token = self._auth.issue_jwt(user.user_id)
        return user, token

    def identify(self, token: str | None) -> str | None:
        """解码 token → user_id；空 / 无效一律返回 None。"""
        if not token:
            return None
        return self._auth.verify_jwt(token)

    def require(self, token: str | None) -> str:
        """同 ``identify`` 但失败抛 InvalidToken；供受保护 API 强校验。"""
        uid = self.identify(token)
        if uid is None:
            raise InvalidToken("missing or invalid token")
        return uid
