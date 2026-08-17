"""JwtIssuer 单元测试：签发 / 校验 / 过期 / 篡改 / 边界。"""

from __future__ import annotations

import jwt
import pytest

from infra.auth.jwt_issuer import JwtIssuer

_SECRET_A = "0123456789abcdef0123456789abcdef"
_SECRET_B = "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz"


class _Clock:
    def __init__(self, now: float = 1_700_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class TestConstructor:
    def test_short_secret_rejected(self) -> None:
        with pytest.raises(ValueError):
            JwtIssuer(secret="short", ttl_seconds=60)

    def test_negative_ttl_rejected(self) -> None:
        with pytest.raises(ValueError):
            JwtIssuer(secret=_SECRET_A, ttl_seconds=0)


class TestIssueAndVerify:
    def test_round_trip(self) -> None:
        issuer = JwtIssuer(secret=_SECRET_A)
        token = issuer.issue("github:alice")
        assert issuer.verify(token) == "github:alice"

    def test_payload_contains_sub_iat_exp(self) -> None:
        clock = _Clock(now=1000.0)
        issuer = JwtIssuer(secret=_SECRET_A, ttl_seconds=3600, clock=clock)
        token = issuer.issue("anon:1234")
        decoded = jwt.decode(token, _SECRET_A, algorithms=["HS256"], options={"verify_exp": False})
        assert decoded["sub"] == "anon:1234"
        assert decoded["iat"] == 1000
        assert decoded["exp"] == 4600

    def test_empty_user_id_rejected(self) -> None:
        issuer = JwtIssuer(secret=_SECRET_A)
        with pytest.raises(ValueError):
            issuer.issue("")

    def test_empty_token_returns_none(self) -> None:
        issuer = JwtIssuer(secret=_SECRET_A)
        assert issuer.verify("") is None

    def test_garbage_token_returns_none(self) -> None:
        issuer = JwtIssuer(secret=_SECRET_A)
        assert issuer.verify("not-a-jwt") is None

    def test_expired_token_returns_none(self) -> None:
        clock = _Clock(now=1000.0)
        issuer = JwtIssuer(secret=_SECRET_A, ttl_seconds=10, clock=clock)
        token = issuer.issue("u1")
        clock.now = 2000.0
        assert issuer.verify(token) is None

    def test_tampered_token_returns_none(self) -> None:
        issuer = JwtIssuer(secret=_SECRET_A)
        token = issuer.issue("u1")
        header, payload, signature = token.split(".")
        # Base64URL 最后一位可能只改变未使用的 padding bit；改签名首字符可稳定改变字节。
        tampered_signature = ("A" if signature[0] != "A" else "B") + signature[1:]
        bad = ".".join((header, payload, tampered_signature))
        assert issuer.verify(bad) is None

    def test_wrong_secret_returns_none(self) -> None:
        issuer_a = JwtIssuer(secret=_SECRET_A)
        issuer_b = JwtIssuer(secret=_SECRET_B)
        token = issuer_a.issue("u1")
        assert issuer_b.verify(token) is None
