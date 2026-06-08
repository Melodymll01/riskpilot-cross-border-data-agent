"""MemorySettingsUseCase 测试：记忆开关读写 + 审计（S-031a）。"""

from __future__ import annotations

import pytest

from app.use_cases.memory_settings import MemorySettingsUseCase
from domain.models import AuditAction, MemorySettings
from tests.fakes.fake_audit_log import FakeAuditLogRepo
from tests.fakes.fake_memory_settings_store import InMemoryMemorySettingsStore

pytestmark = pytest.mark.unit


class TestGet:
    def test_no_store_returns_default_both_on(self) -> None:
        uc = MemorySettingsUseCase(None)
        s = uc.get("o1")
        assert s.use_saved_memory is True
        assert s.reference_history is True

    def test_unset_owner_returns_default_both_on(self) -> None:
        uc = MemorySettingsUseCase(InMemoryMemorySettingsStore())
        s = uc.get("o1")
        assert s.use_saved_memory is True
        assert s.reference_history is True

    def test_returns_persisted(self) -> None:
        store = InMemoryMemorySettingsStore()
        store.upsert(
            MemorySettings(owner_id="o1", use_saved_memory=False, reference_history=True)
        )
        uc = MemorySettingsUseCase(store)
        s = uc.get("o1")
        assert s.use_saved_memory is False
        assert s.reference_history is True


class TestUpdate:
    def test_partial_update_keeps_unset_field(self) -> None:
        store = InMemoryMemorySettingsStore()
        store.upsert(
            MemorySettings(owner_id="o1", use_saved_memory=True, reference_history=True)
        )
        uc = MemorySettingsUseCase(store)

        updated = uc.update("o1", use_saved_memory=False)

        assert updated.use_saved_memory is False
        assert updated.reference_history is True  # 未传 → 保持
        # 已持久化
        assert store.get("o1").use_saved_memory is False

    def test_update_both(self) -> None:
        store = InMemoryMemorySettingsStore()
        uc = MemorySettingsUseCase(store)

        updated = uc.update("o1", use_saved_memory=False, reference_history=False)

        assert updated.use_saved_memory is False
        assert updated.reference_history is False

    def test_update_records_audit(self) -> None:
        audit = FakeAuditLogRepo()
        uc = MemorySettingsUseCase(InMemoryMemorySettingsStore(), audit_log=audit)

        uc.update("anon:o1", use_saved_memory=False)

        assert len(audit.entries) == 1
        e = audit.entries[0]
        assert e.action == AuditAction.MEMORY_SETTINGS_UPDATE
        assert e.actor_id == "anon:o1"
        assert e.resource == "anon:o1"
        assert e.success is True
        assert e.extra_json["use_saved_memory"] is False
        assert e.extra_json["persisted"] is True

    def test_no_store_update_is_silent_not_persisted(self) -> None:
        audit = FakeAuditLogRepo()
        uc = MemorySettingsUseCase(None, audit_log=audit)

        updated = uc.update("o1", reference_history=False)

        assert updated.reference_history is False
        assert audit.entries[0].extra_json["persisted"] is False

    def test_no_audit_log_is_silent(self) -> None:
        uc = MemorySettingsUseCase(InMemoryMemorySettingsStore(), audit_log=None)
        updated = uc.update("o1", use_saved_memory=False)  # 不抛
        assert updated.use_saved_memory is False
