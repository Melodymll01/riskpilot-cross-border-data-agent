"""AnonymousProvider 单元测试。"""

from __future__ import annotations

from infra.auth.anonymous import AnonymousProvider


class TestAnonymousProvider:
    def test_user_id_namespace(self) -> None:
        u = AnonymousProvider().create()
        assert u.user_id.startswith("anon:")
        # uuid4().hex[:16] = 16 个十六进制字符
        suffix = u.user_id.removeprefix("anon:")
        assert len(suffix) == 16
        assert all(c in "0123456789abcdef" for c in suffix)

    def test_provider_anonymous(self) -> None:
        u = AnonymousProvider().create()
        assert u.provider == "anonymous"
        assert u.provider_id == u.user_id.removeprefix("anon:")

    def test_email_and_avatar_none(self) -> None:
        u = AnonymousProvider().create()
        assert u.email is None
        assert u.avatar_url is None

    def test_created_at_equals_last_active_at(self) -> None:
        u = AnonymousProvider().create()
        assert u.created_at == u.last_active_at

    def test_uniqueness(self) -> None:
        provider = AnonymousProvider()
        ids = {provider.create().user_id for _ in range(10)}
        assert len(ids) == 10

    def test_clock_injection(self) -> None:
        provider = AnonymousProvider(clock=lambda: 999.0)
        u = provider.create()
        assert u.created_at == 999.0
        assert u.last_active_at == 999.0
