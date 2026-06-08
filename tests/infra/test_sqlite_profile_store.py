"""SqliteProfileStore 集成测试（临时文件 DB，S-030d）。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from domain.models import SessionProfile
from domain.ports import ProfileStorePort
from infra.storage import SqliteProfileStore
from infra.storage._db import SqliteConnectionPool

pytestmark = pytest.mark.integration


@pytest.fixture
def pool(tmp_path: Path) -> SqliteConnectionPool:
    return SqliteConnectionPool(str(tmp_path / "app.db"))


@pytest.fixture
def store(pool: SqliteConnectionPool) -> SqliteProfileStore:
    return SqliteProfileStore(pool)


def _profile(owner_id: str, facts: dict) -> SessionProfile:
    return SessionProfile(owner_id=owner_id, facts=facts, updated_at=time.time())


class TestProtocolConformance:
    def test_is_profile_store_port(self, store: SqliteProfileStore) -> None:
        assert isinstance(store, ProfileStorePort)


class TestGetUpsert:
    def test_get_missing_returns_none(self, store: SqliteProfileStore) -> None:
        assert store.get("o1") is None

    def test_upsert_then_get_roundtrip(self, store: SqliteProfileStore) -> None:
        store.upsert(_profile("o1", {"语言": "中文", "行业": "跨境电商"}))

        rec = store.get("o1")
        assert rec is not None
        assert rec.owner_id == "o1"
        assert rec.facts == {"语言": "中文", "行业": "跨境电商"}

    def test_upsert_overwrites(self, store: SqliteProfileStore) -> None:
        store.upsert(_profile("o1", {"语言": "中文"}))
        store.upsert(_profile("o1", {"语言": "英文"}))

        rec = store.get("o1")
        assert rec is not None
        assert rec.facts == {"语言": "英文"}

    def test_owner_isolation(self, store: SqliteProfileStore) -> None:
        store.upsert(_profile("o1", {"k": "v1"}))
        store.upsert(_profile("o2", {"k": "v2"}))

        assert store.get("o1").facts == {"k": "v1"}  # type: ignore[union-attr]
        assert store.get("o2").facts == {"k": "v2"}  # type: ignore[union-attr]


class TestDeleteOwner:
    def test_delete_existing_returns_one(self, store: SqliteProfileStore) -> None:
        store.upsert(_profile("o1", {"k": "v"}))
        assert store.delete_owner("o1") == 1
        assert store.get("o1") is None

    def test_delete_missing_returns_zero(self, store: SqliteProfileStore) -> None:
        assert store.delete_owner("ghost") == 0

    def test_delete_only_target_owner(self, store: SqliteProfileStore) -> None:
        store.upsert(_profile("o1", {"k": "v1"}))
        store.upsert(_profile("o2", {"k": "v2"}))

        store.delete_owner("o1")

        assert store.get("o1") is None
        assert store.get("o2") is not None
