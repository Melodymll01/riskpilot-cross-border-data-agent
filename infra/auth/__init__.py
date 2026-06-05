"""Infra/auth：AuthPort 的具体实现。

- `JwtIssuer`：PyJWT HS256 签发与校验
- `AnonymousProvider`：匿名 user_id 生成
- `GitHubOAuthProvider`：GitHub OAuth 2.0 三段式
- `AuthService`：组合上面 3 个，实现 `domain.ports.AuthPort`
"""

from infra.auth.anonymous import AnonymousProvider
from infra.auth.auth_service import AuthService
from infra.auth.github_oauth import GitHubOAuthProvider
from infra.auth.jwt_issuer import JwtIssuer

__all__ = [
    "AnonymousProvider",
    "AuthService",
    "GitHubOAuthProvider",
    "JwtIssuer",
]
