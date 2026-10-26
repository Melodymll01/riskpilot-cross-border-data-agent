"""The private Python service accepts only request-bound Go-issued identities."""

import secrets
import time

import jwt
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from domain.models import User
from infra.auth.gateway_identity import GatewayIdentityMiddleware, ReadOnlyGatewayUsers
from tests.fakes import InMemoryUserRepo


@pytest.fixture
def private_service(tmp_path):
    secret = "test-service-" * 6
    key = tmp_path / "service.key"
    key.write_text(secret)
    users = InMemoryUserRepo()
    users.upsert(
        User(
            user_id="github:alice",
            provider="github",
            provider_id="alice",
            display_name="Alice",
            created_at=1,
            last_active_at=1,
        )
    )
    app = FastAPI()
    app.add_middleware(
        GatewayIdentityMiddleware, key_file=str(key), users=ReadOnlyGatewayUsers(users)
    )

    @app.api_route("/api/v2/memory", methods=["GET", "POST"])
    def memory(request: Request):
        return {"owner": request.state.gateway_owner}

    @app.get("/api/v2/health/live")
    def live():
        return {"status": "ok"}

    return TestClient(app), secret, users


def signed(secret, *, method="GET", target="/api/v2/memory", owner="github:alice", **overrides):
    now = int(time.time())
    claims = {
        "iss": "riskpilot-go",
        "aud": "riskpilot-agent",
        "sub": owner,
        "iat": now,
        "exp": now + 30,
        "jti": secrets.token_hex(24),
        "method": method,
        "target": target,
        **overrides,
    }
    return {"X-Riskpilot-Identity": jwt.encode(claims, secret, algorithm="HS256")}


def test_private_service_requires_signature_and_never_uses_browser_cookie(private_service):
    client, secret, _ = private_service
    assert client.get("/api/v2/health/live").status_code == 200
    assert (
        client.get(
            "/api/v2/memory",
            headers={"X-Riskpilot-Owner": "github:alice", "Cookie": "copilot_session=forged"},
        ).status_code
        == 401
    )
    assert client.get("/api/v2/auth/anonymous", headers=signed(secret)).status_code == 404
    assert client.get("/api/v2/memory", headers=signed(secret)).json() == {"owner": "github:alice"}


@pytest.mark.parametrize(
    "override",
    [
        {"method": "POST"},
        {"target": "/api/v2/memory?query=wrong"},
        {"owner": "github:bob"},
        {"aud": "browser"},
        {"iss": "attacker"},
        {"exp": int(time.time()) - 1},
        {"exp": int(time.time()) + 3600},
    ],
)
def test_scope_binding_unknown_subject_and_lifetime_are_rejected(private_service, override):
    client, secret, _ = private_service
    assert client.get("/api/v2/memory", headers=signed(secret, **override)).status_code == 401


def test_nonce_cannot_be_replayed(private_service):
    client, secret, _ = private_service
    headers = signed(secret)
    assert client.get("/api/v2/memory", headers=headers).status_code == 200
    assert client.get("/api/v2/memory", headers=headers).status_code == 401


def test_python_gateway_user_port_is_read_only(private_service):
    _, _, users = private_service
    readonly = ReadOnlyGatewayUsers(users)
    assert readonly.get("github:alice") is not None
    with pytest.raises(RuntimeError, match="Go owns"):
        readonly.upsert(users.get("github:alice"))
    with pytest.raises(RuntimeError, match="Go owns"):
        readonly.touch("github:alice")
