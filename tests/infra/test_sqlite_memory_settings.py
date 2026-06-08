"""SqliteMemorySettingsStore 集成测试（临时文件 DB，S-031a）。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from domain.models import MemorySettings
from domain.ports import MemorySettingsStorePort
from infra.storage import SqliteMemorySettingsStore
from infra.storage._db import SqliteConnectionPool

pytestmark = pytest.mark.integration


@pytest.fixture
def pool(tmp_path: Path) -> SqliteConnectionPool:
    return SqliteConnectionPool(str(tmp_path / "app.db"))


@pytest.fixture
def store(pool: SqliteConnectionPool) -> SqliteMemorySettingsStore:
    return SqliteMemorySettingsStore(pool)


def _settings(owner_id: str, use_saved: bool, ref_hist: bool) -> MemorySettings:
    return MemorySettings(
        owner_id=owner_id,
        use_saved_memory=use_saved,
        reference_history=ref_hist,
        updated_at=time.time(),
    )


class TestProtocolConformance:
    def test_is_settings_store_port(self, store: SqliteMemorySettingsStore) -> None:
        assert isinstance(store, MemorySettingsStorePort)


class TestGetUpsert:
    def test_get_missing_returns_none(self, store: SqliteMemorySettingsStore) -> None:
        assert store.get("o1") is None

    def test_upsert_then_get_roundtrip(self, store: SqliteMemorySettingsStore) -> None:
        store.upsert(_settings("o1", use_saved=False, ref_hist=True))

        rec = store.get("o1")
        assert rec is not None
        assert rec.owner_id == "o1"
        assert rec.use_saved_memory is False
        assert rec.reference_history is True

    def test_bool_roundtrip_both_false(self, store: SqliteMemorySettingsStore) -> None:
        store.upsert(_settings("o1", use_saved=False, ref_hist=False))
        rec = store.get("o1")
        assert rec is not None
        assert rec.use_saved_memory is False
        assert rec.reference_history is False

    def test_upsert_overwrites(self, store: SqliteMemorySettingsStore) -> None:
        store.upsert(_settings("o1", use_saved=True, ref_hist=True))
        store.upsert(_settings("o1", use_saved=False, ref_hist=False))

        rec = store.get("o1")
        assert rec is not None
        assert rec.use_saved_memory is False
        assert rec.reference_history is False

    def test_owner_isolation(self, store: SqliteMemorySettingsStore) -> None:
        store.upsert(_settings("o1", use_saved=False, ref_hist=True))
        store.upsert(_settings("o2", use_saved=True, ref_hist=False))

        assert store.get("o1").use_saved_memory is False
        assert store.get("o2").use_saved_memory is True
