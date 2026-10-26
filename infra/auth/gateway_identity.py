"""Validate request-bound Go service identities without trusting browser cookies."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import jwt
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from domain.models import User
from domain.ports import UserRepoPort

IDENTITY_HEADER = b"x-riskpilot-identity"
PUBLIC_PROBES = frozenset({"/api/v2/health", "/api/v2/health/live", "/api/v2/health/ready"})
SERVICE_ONLY = frozenset({"/docs", "/redoc", "/openapi.json"})


class GatewayIdentityMiddleware:
    def __init__(self, app: ASGIApp, *, key_file: str, users: UserRepoPort) -> None:
        self.app = app
        self.users = users
        self.key = Path(key_file).read_text().strip()
        if len(self.key) < 32:
            raise ValueError("internal service key must have at least 32 characters")
        self._nonces: dict[str, float] = {}
        self._lock = threading.Lock()

    def _claims(self, scope: Scope) -> dict[str, Any]:
        values = [
            value.decode()
            for name, value in scope.get("headers", [])
            if name.lower() == IDENTITY_HEADER
        ]
        if len(values) != 1:
            raise ValueError("missing or duplicate service identity")
        claims = jwt.decode(
            values[0],
            self.key,
            algorithms=["HS256"],
            issuer="riskpilot-go",
            audience="riskpilot-agent",
            options={"require": ["exp", "iat", "iss", "aud", "sub", "jti", "method", "target"]},
        )
        issued, expiry = claims["iat"], claims["exp"]
        if (
            not isinstance(issued, int)
            or not isinstance(expiry, int)
            or not 0 < expiry - issued <= 30
        ):
            raise ValueError("invalid service identity lifetime")
        path = scope.get("raw_path", scope["path"].encode()).decode("ascii")
        query = scope.get("query_string", b"").decode("ascii")
        target = path + ("?" + query if query else "")
        if claims["method"] != scope["method"] or claims["target"] != target:
            raise ValueError("service identity request mismatch")
        owner, nonce = claims["sub"], claims["jti"]
        if not isinstance(owner, str) or not isinstance(nonce, str) or not 16 <= len(nonce) <= 128:
            raise ValueError("invalid service subject or nonce")
        if not owner and scope["path"] not in SERVICE_ONLY:
            raise ValueError("owner required for business request")
        if owner and self.users.get(owner) is None:
            raise ValueError("unknown service subject")
        now = time.time()
        with self._lock:
            self._nonces = {key: until for key, until in self._nonces.items() if until > now}
            if nonce in self._nonces or len(self._nonces) >= 10000:
                raise ValueError("duplicate service nonce or capacity exceeded")
            self._nonces[nonce] = expiry
        return claims

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if scope["path"] in PUBLIC_PROBES and scope["method"] == "GET":
            await self.app(scope, receive, send)
            return
        if scope["path"].startswith("/api/v2/auth/"):
            await JSONResponse(
                {"error_code": "AUTH_MOVED_TO_GO", "message": "认证由 Go 服务处理"}, status_code=404
            )(scope, receive, send)
            return
        try:
            claims = self._claims(scope)
        except (jwt.PyJWTError, ValueError, UnicodeError):
            await JSONResponse(
                {"error_code": "SERVICE_AUTH_REQUIRED", "message": "需要有效的内部服务身份"},
                status_code=401,
            )(scope, receive, send)
            return
        state = scope.setdefault("state", {})
        state["gateway_owner"] = claims["sub"] or None
        await self.app(scope, receive, send)


class ReadOnlyGatewayUsers:
    """Go owns users during phase 1; Python only reads their resource identities."""

    def __init__(self, repo: UserRepoPort) -> None:
        self._repo = repo

    def get(self, user_id: str) -> User | None:
        return self._repo.get(user_id)

    def upsert(self, user: User) -> None:
        raise RuntimeError("Go owns user writes")

    def touch(self, user_id: str) -> None:
        raise RuntimeError("Go owns user writes")

    def merge_owner(self, from_id: str, to_id: str) -> int:
        raise RuntimeError("Go owns identity changes")


class GatewayOnlyAuth:
    """The internal runtime cannot issue or consume browser sessions."""

    def begin_oauth(self, provider: str) -> tuple[str, str]:
        raise RuntimeError("OAuth moved to Go")

    def complete_oauth(self, provider: str, code: str, state: str) -> User:
        raise RuntimeError("OAuth moved to Go")

    def issue_jwt(self, user_id: str) -> str:
        raise RuntimeError("Session signing moved to Go")

    def verify_jwt(self, token: str) -> str | None:
        return None

    def create_anonymous(self) -> User:
        raise RuntimeError("Anonymous login moved to Go")
