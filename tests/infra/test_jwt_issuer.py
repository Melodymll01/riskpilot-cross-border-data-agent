"""JwtIssuer 单元测试：签发 / 校验 / 过期 / 篡改 / 边界。"""

from __future__ import annotations

import jwt
import pytest

from infra.auth.jwt_issuer import JwtIssuer


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
            JwtIssuer(secret="0123456789abcdef", ttl_seconds=0)


class TestIssueAndVerify:
    def test_round_trip(self) -> None:
        issuer = JwtIssuer(secret="0123456789abcdef")
        token = issuer.issue("github:alice")
        assert issuer.verify(token) == "github:alice"

    def test_payload_contains_sub_iat_exp(self) -> None:
        clock = _Clock(now=1000.0)
        issuer = JwtIssuer(secret="0123456789abcdef", ttl_seconds=3600, clock=clock)
        token = issuer.issue("anon:1234")
        decoded = jwt.decode(
            token, "0123456789abcdef", algorithms=["HS256"], options={"verify_exp": False}
        )
        assert decoded["sub"] == "anon:1234"
        assert decoded["iat"] == 1000
        assert decoded["exp"] == 4600

    def test_empty_user_id_rejected(self) -> None:
        issuer = JwtIssuer(secret="0123456789abcdef")
        with pytest.raises(ValueError):
            issuer.issue("")

    def test_empty_token_returns_none(self) -> None:
        issuer = JwtIssuer(secret="0123456789abcdef")
        assert issuer.verify("") is None

    def test_garbage_token_returns_none(self) -> None:
        issuer = JwtIssuer(secret="0123456789abcdef")
        assert issuer.verify("not-a-jwt") is None

    def test_expired_token_returns_none(self) -> None:
        clock = _Clock(now=1000.0)
        issuer = JwtIssuer(secret="0123456789abcdef", ttl_seconds=10, clock=clock)
        token = issuer.issue("u1")
        clock.now = 2000.0
        assert issuer.verify(token) is None

    def test_tampered_token_returns_none(self) -> None:
        issuer = JwtIssuer(secret="0123456789abcdef")
        token = issuer.issue("u1")
        # 改最后一个字符破坏签名
        bad = token[:-1] + ("A" if token[-1] != "A" else "B")
        assert issuer.verify(bad) is None

    def test_wrong_secret_returns_none(self) -> None:
        issuer_a = JwtIssuer(secret="0123456789abcdef")
        issuer_b = JwtIssuer(secret="zzzzzzzzzzzzzzzz")
        token = issuer_a.issue("u1")
        assert issuer_b.verify(token) is None
