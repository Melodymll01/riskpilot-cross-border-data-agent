"""Request-bound Go service-principal authentication."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import jwt
from pydantic import ValidationError
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from domain.runtime import ServicePrincipal

IDENTITY_HEADER = b"x-riskpilot-identity"


class ServicePrincipalMiddleware:
    """Validate request-bound identities issued by the Go control plane."""

    def __init__(self, app: ASGIApp, *, key_file: str) -> None:
        self.app = app
        self.key = Path(key_file).read_text().strip()
        if len(self.key) < 32:
            raise ValueError("internal service key must have at least 32 characters")
        self._nonces: dict[str, float] = {}
        self._lock = threading.Lock()

    def _claims(self, scope: Scope) -> ServicePrincipal:
        values = [
            value.decode()
            for name, value in scope.get("headers", [])
            if name.lower() == IDENTITY_HEADER
        ]
        if len(values) != 1:
            raise ValueError("missing or duplicate service identity")
        raw: dict[str, Any] = jwt.decode(
            values[0],
            self.key,
            algorithms=["HS256"],
            issuer="riskpilot-go",
            audience="riskpilot-agent",
            options={"require": ["exp", "iat", "iss", "aud", "sub", "jti", "method", "target"]},
        )
        principal = ServicePrincipal.model_validate(raw)
        path = scope.get("raw_path", scope["path"].encode()).decode("ascii")
        query = scope.get("query_string", b"").decode("ascii")
        target = path + ("?" + query if query else "")
        if principal.method != scope["method"] or principal.target != target:
            raise ValueError("service identity request mismatch")
        if scope["path"].startswith("/internal/v1/runs/") and not principal.sub:
            raise ValueError("owner subject required for run request")
        now = time.time()
        with self._lock:
            self._nonces = {key: until for key, until in self._nonces.items() if until > now}
            if principal.jti in self._nonces or len(self._nonces) >= 10_000:
                raise ValueError("duplicate service nonce or nonce capacity exceeded")
            self._nonces[principal.jti] = principal.exp
        return principal

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        try:
            principal = self._claims(scope)
        except (jwt.PyJWTError, ValidationError, ValueError, UnicodeError):
            await JSONResponse(
                {
                    "error_code": "SERVICE_AUTH_REQUIRED",
                    "message": "valid service identity required",
                },
                status_code=401,
            )(scope, receive, send)
            return
        scope.setdefault("state", {})["service_principal"] = principal
        await self.app(scope, receive, send)
