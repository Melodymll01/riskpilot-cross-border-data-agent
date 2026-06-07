"""SqliteSummaryStore 集成测试（临时文件 DB，S-030b）。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from domain.models import Task, TaskSummary
from domain.ports import SummaryStorePort
from infra.storage import SqliteSummaryStore, SqliteTaskRepo
from infra.storage._db import SqliteConnectionPool

pytestmark = pytest.mark.integration


@pytest.fixture
def pool(tmp_path: Path) -> SqliteConnectionPool:
    return SqliteConnectionPool(str(tmp_path / "app.db"))


@pytest.fixture
def task_repo(pool: SqliteConnectionPool) -> SqliteTaskRepo:
    return SqliteTaskRepo(pool)


@pytest.fixture
def store(pool: SqliteConnectionPool) -> SqliteSummaryStore:
    return SqliteSummaryStore(pool)


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


def _summary(task_id: str, owner_id: str, text: str, wm: int = 3) -> TaskSummary:
    return TaskSummary(
        task_id=task_id,
        owner_id=owner_id,
        summary=text,
        msg_watermark=wm,
        updated_at=time.time(),
    )


class TestProtocolConformance:
    def test_is_summary_store_port(self, store: SqliteSummaryStore) -> None:
        assert isinstance(store, SummaryStorePort)


class TestGetUpsert:
    def test_get_missing_returns_none(
        self, store: SqliteSummaryStore, task_repo: SqliteTaskRepo
    ) -> None:
        _seed_task(task_repo, task_id="t1", owner_id="o1")
        assert store.get("t1", "o1") is None

    def test_upsert_then_get_roundtrip(
        self, store: SqliteSummaryStore, task_repo: SqliteTaskRepo
    ) -> None:
        _seed_task(task_repo, task_id="t1", owner_id="o1")
        store.upsert(_summary("t1", "o1", "摘要A", wm=5))

        rec = store.get("t1", "o1")
        assert rec is not None
        assert rec.summary == "摘要A"
        assert rec.msg_watermark == 5

    def test_upsert_conflict_updates_in_place(
        self, store: SqliteSummaryStore, task_repo: SqliteTaskRepo
    ) -> None:
        _seed_task(task_repo, task_id="t1", owner_id="o1")
        store.upsert(_summary("t1", "o1", "旧", wm=3))
        store.upsert(_summary("t1", "o1", "新", wm=7))

        rec = store.get("t1", "o1")
        assert rec is not None
        assert rec.summary == "新"
        assert rec.msg_watermark == 7


class TestOwnerIsolation:
    def test_other_owner_cannot_read(
        self, store: SqliteSummaryStore, task_repo: SqliteTaskRepo
    ) -> None:
        _seed_task(task_repo, task_id="t1", owner_id="owner_a")
        store.upsert(_summary("t1", "owner_a", "机密"))

        assert store.get("t1", "owner_b") is None
