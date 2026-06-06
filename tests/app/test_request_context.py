"""``app.request_context`` 单测（Step 025d）。

测点：
- 默认值 ``None``，无 set/middleware 时 ``get_request_id() is None``
- ``set_request_id`` 返回的 token 可 ``reset`` 还原
- ``request_context`` contextmanager 进入/退出语义
- use case 的 ``_record_audit`` fallback：形参传 None 时取 contextvar
- 形参显式给值时**不**被 contextvar 覆盖
- ``audit_log=None`` 仍然安全（向后兼容）
"""

from __future__ import annotations

from app.request_context import (
    get_request_id,
    request_context,
    reset_request_id,
    set_request_id,
)
from app.use_cases.auth_login import AuthLoginUseCase
from app.use_cases.kb_management import KbManagementUseCase
from tests.fakes import (
    FakeAuditLogRepo,
    FakeAuth,
    FakeDocumentLoader,
    FakeEmbed,
    FakeKbRepo,
)


class TestContextVarPrimitive:
    def test_default_is_none(self) -> None:
        # 进入测试时 contextvar 应为 None（pytest 默认 task）
        assert get_request_id() is None

    def test_set_and_reset(self) -> None:
        token = set_request_id("req-abc")
        assert get_request_id() == "req-abc"
        reset_request_id(token)
        assert get_request_id() is None

    def test_request_context_manager(self) -> None:
        assert get_request_id() is None
        with request_context("req-xyz"):
            assert get_request_id() == "req-xyz"
        assert get_request_id() is None

    def test_nested_contexts_restore_outer(self) -> None:
        with request_context("outer"):
            assert get_request_id() == "outer"
            with request_context("inner"):
                assert get_request_id() == "inner"
            assert get_request_id() == "outer"
        assert get_request_id() is None

    def test_context_isolated_on_exception(self) -> None:
        try:
            with request_context("rid"):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        # 异常退出仍应 reset
        assert get_request_id() is None


# ─────────────── use case 集成：auth_login ───────────────


class TestAuthLoginFallback:
    def _uc(self) -> tuple[AuthLoginUseCase, FakeAuditLogRepo]:
        audit = FakeAuditLogRepo()
        uc = AuthLoginUseCase(FakeAuth(), audit_log=audit)
        return uc, audit

    def test_contextvar_fills_when_explicit_is_none(self) -> None:
        uc, audit = self._uc()
        with request_context("req-from-ctx"):
            uc.login_anonymous()  # 不传 request_id
        assert len(audit.entries) == 1
        assert audit.entries[0].request_id == "req-from-ctx"

    def test_explicit_request_id_wins_over_contextvar(self) -> None:
        uc, audit = self._uc()
        with request_context("req-from-ctx"):
            uc.login_anonymous(request_id="req-explicit")
        assert audit.entries[0].request_id == "req-explicit"

    def test_no_contextvar_no_explicit_yields_none(self) -> None:
        uc, audit = self._uc()
        uc.login_anonymous()  # contextvar 默认 None
        assert audit.entries[0].request_id is None


# ─────────────── use case 集成：kb_management ───────────────


class TestKbManagementFallback:
    def _uc(self) -> tuple[KbManagementUseCase, FakeAuditLogRepo]:
        audit = FakeAuditLogRepo()
        uc = KbManagementUseCase(
            kb_repo=FakeKbRepo(),
            loader=FakeDocumentLoader(),
            embedder=FakeEmbed(),
            audit_log=audit,
        )
        return uc, audit

    def test_ingest_web_picks_up_contextvar(self) -> None:
        uc, audit = self._uc()
        with request_context("req-kb-ingest"):
            uc.ingest_web("https://example.com/x", actor_id="github:alice")
        # 至少一条带上 request_id
        assert audit.entries, "应至少有一条审计记录"
        assert all(e.request_id == "req-kb-ingest" for e in audit.entries)

    def test_delete_picks_up_contextvar(self) -> None:
        uc, audit = self._uc()
        with request_context("req-kb-del"):
            uc.delete_document("not-exist.pdf", actor_id="github:alice", actor_is_admin=True)
        assert audit.entries
        assert audit.entries[0].request_id == "req-kb-del"

    def test_audit_log_none_still_safe(self) -> None:
        # 与 Step 021 / 025c 的向后兼容契约保持一致
        uc = KbManagementUseCase(
            kb_repo=FakeKbRepo(),
            loader=FakeDocumentLoader(),
            embedder=FakeEmbed(),
            audit_log=None,
        )
        with request_context("req-noop"):
            uc.delete_document("x.pdf", actor_id="github:alice", actor_is_admin=True)
        # 无 audit 端口 → 不抛
