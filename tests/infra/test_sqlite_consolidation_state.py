"""SqliteConsolidationStateStore 集成测试（临时文件 DB，S-030c）。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from domain.models import ConsolidationState, Task
from domain.ports import ConsolidationStatePort
from infra.storage import SqliteConsolidationStateStore, SqliteTaskRepo
from infra.storage._db import SqliteConnectionPool

pytestmark = pytest.mark.integration


@pytest.fixture
def pool(tmp_path: Path) -> SqliteConnectionPool:
    return SqliteConnectionPool(str(tmp_path / "app.db"))


@pytest.fixture
def task_repo(pool: SqliteConnectionPool) -> SqliteTaskRepo:
    return SqliteTaskRepo(pool)


@pytest.fixture
def store(pool: SqliteConnectionPool) -> SqliteConsolidationStateStore:
    return SqliteConsolidationStateStore(pool)


def _seed_task(repo: SqliteTaskRepo, *, task_id: str, owner_id: str) -> None:
    t = time.time()
    repo.create(
        Task(
            task_id=task_id,
            owner_id=owner_id,
            title="t",
            state="planning",
            user_goal="",
            collected_facts={},
            created_at=t,
            updated_at=t,
        )
    )


def _state(task_id: str, owner_id: str, wm: int) -> ConsolidationState:
    return ConsolidationState(
        task_id=task_id,
        owner_id=owner_id,
        msg_watermark=wm,
        updated_at=time.time(),
    )


class TestProtocolConformance:
    def test_is_consolidation_state_port(
        self, store: SqliteConsolidationStateStore
    ) -> None:
        assert isinstance(store, ConsolidationStatePort)


class TestGetUpsert:
    def test_get_missing_returns_none(
        self, store: SqliteConsolidationStateStore, task_repo: SqliteTaskRepo
    ) -> None:
        _seed_task(task_repo, task_id="t1", owner_id="o1")
        assert store.get("t1", "o1") is None

    def test_upsert_then_get_roundtrip(
        self, store: SqliteConsolidationStateStore, task_repo: SqliteTaskRepo
    ) -> None:
        _seed_task(task_repo, task_id="t1", owner_id="o1")
        store.upsert(_state("t1", "o1", 5))

        rec = store.get("t1", "o1")
        assert rec is not None
        assert rec.msg_watermark == 5

    def test_upsert_conflict_updates_in_place(
        self, store: SqliteConsolidationStateStore, task_repo: SqliteTaskRepo
    ) -> None:
        _seed_task(task_repo, task_id="t1", owner_id="o1")
        store.upsert(_state("t1", "o1", 3))
        store.upsert(_state("t1", "o1", 9))

        rec = store.get("t1", "o1")
        assert rec is not None
        assert rec.msg_watermark == 9


class TestOwnerIsolation:
    def test_other_owner_cannot_read(
        self, store: SqliteConsolidationStateStore, task_repo: SqliteTaskRepo
    ) -> None:
        _seed_task(task_repo, task_id="t1", owner_id="owner_a")
        store.upsert(_state("t1", "owner_a", 4))

        assert store.get("t1", "owner_b") is None
