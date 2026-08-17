"""SqliteAuditLogRepo 集成测试（临时文件 DB）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from domain import AuditAction, AuditEntry
from domain.ports import AuditLogPort
from infra.audit import SqliteAuditLogRepo
from infra.storage._db import SqliteConnectionPool


@pytest.fixture
def pool(tmp_path: Path) -> SqliteConnectionPool:
    return SqliteConnectionPool(str(tmp_path / "audit.db"))


@pytest.fixture
def repo(pool: SqliteConnectionPool) -> SqliteAuditLogRepo:
    return SqliteAuditLogRepo(pool)


def _entry(
    *,
    actor_id: str = "github:Melodymll01",
    action: str = AuditAction.KB_DELETE,
    resource: str = "doc.pdf",
    timestamp: float = 1_700_000_000.0,
    success: bool = True,
    error: str | None = None,
    request_id: str | None = None,
    extra: dict[str, object] | None = None,
) -> AuditEntry:
    return AuditEntry(
        actor_id=actor_id,
        action=action,
        resource=resource,
        timestamp=timestamp,
        success=success,
        error=error,
        request_id=request_id,
        extra_json=extra or {},
    )


class TestRecordAndList:
    def test_round_trip(self, repo: SqliteAuditLogRepo) -> None:
        e = _entry(extra={"deleted_count": 3, "category": "law"})
        repo.record(e)
        rows = repo.list_recent(limit=10)
        assert len(rows) == 1
        got = rows[0]
        assert got.actor_id == e.actor_id
        assert got.action == e.action
        assert got.resource == e.resource
        assert got.timestamp == e.timestamp
        assert got.success is True
        assert got.error is None
        assert got.extra_json == {"deleted_count": 3, "category": "law"}

    def test_failure_entry_persisted(self, repo: SqliteAuditLogRepo) -> None:
        repo.record(_entry(success=False, error="boom"))
        [got] = repo.list_recent(limit=10)
        assert got.success is False
        assert got.error == "boom"

    def test_request_id_persisted(self, repo: SqliteAuditLogRepo) -> None:
        repo.record(_entry(request_id="req-xyz"))
        [got] = repo.list_recent(limit=10)
        assert got.request_id == "req-xyz"


class TestOrdering:
    def test_descending_by_timestamp(self, repo: SqliteAuditLogRepo) -> None:
        repo.record(_entry(timestamp=100.0, resource="a"))
        repo.record(_entry(timestamp=300.0, resource="c"))
        repo.record(_entry(timestamp=200.0, resource="b"))
        rows = repo.list_recent(limit=10)
        assert [r.resource for r in rows] == ["c", "b", "a"]

    def test_limit_caps_results(self, repo: SqliteAuditLogRepo) -> None:
        for i in range(5):
            repo.record(_entry(timestamp=float(i), resource=f"r{i}"))
        rows = repo.list_recent(limit=2)
        assert len(rows) == 2
        # 最新的两条
        assert {r.resource for r in rows} == {"r4", "r3"}


class TestFilters:
    def test_filter_by_action(self, repo: SqliteAuditLogRepo) -> None:
        repo.record(_entry(action=AuditAction.KB_DELETE, resource="d"))
        repo.record(_entry(action=AuditAction.KB_INGEST_FILE, resource="f"))
        repo.record(_entry(action=AuditAction.KB_INGEST_WEB, resource="w"))
        rows = repo.list_recent(action=AuditAction.KB_DELETE)
        assert len(rows) == 1
        assert rows[0].resource == "d"

    def test_filter_by_actor(self, repo: SqliteAuditLogRepo) -> None:
        repo.record(_entry(actor_id="github:alice", resource="a"))
        repo.record(_entry(actor_id="github:bob", resource="b"))
        rows = repo.list_recent(actor_id="github:alice")
        assert len(rows) == 1
        assert rows[0].resource == "a"

    def test_filters_combine(self, repo: SqliteAuditLogRepo) -> None:
        repo.record(_entry(actor_id="a", action=AuditAction.KB_DELETE, resource="d1"))
        repo.record(_entry(actor_id="a", action=AuditAction.KB_INGEST_FILE, resource="f1"))
        repo.record(_entry(actor_id="b", action=AuditAction.KB_DELETE, resource="d2"))
        rows = repo.list_recent(actor_id="a", action=AuditAction.KB_DELETE)
        assert len(rows) == 1
        assert rows[0].resource == "d1"


class TestPagination:
    def test_offset_skips_n(self, repo: SqliteAuditLogRepo) -> None:
        for i in range(5):
            repo.record(_entry(timestamp=float(i), resource=f"r{i}"))
        # 倒序下 r4, r3, r2, r1, r0；offset=2 跳过前两个
        rows = repo.list_recent(limit=10, offset=2)
        assert [r.resource for r in rows] == ["r2", "r1", "r0"]

    def test_offset_with_limit_window(self, repo: SqliteAuditLogRepo) -> None:
        for i in range(5):
            repo.record(_entry(timestamp=float(i), resource=f"r{i}"))
        rows = repo.list_recent(limit=2, offset=1)
        assert [r.resource for r in rows] == ["r3", "r2"]

    def test_offset_beyond_total_returns_empty(self, repo: SqliteAuditLogRepo) -> None:
        repo.record(_entry(resource="only"))
        assert repo.list_recent(limit=10, offset=10) == []

    def test_offset_respects_filters(self, repo: SqliteAuditLogRepo) -> None:
        for i in range(4):
            repo.record(
                _entry(
                    timestamp=float(i),
                    action=AuditAction.KB_DELETE,
                    resource=f"d{i}",
                )
            )
        repo.record(_entry(action=AuditAction.KB_INGEST_FILE, resource="f"))
        # 过滤 + 分页同时生效
        rows = repo.list_recent(
            limit=2,
            offset=1,
            action=AuditAction.KB_DELETE,
        )
        # KB_DELETE 倒序：d3, d2, d1, d0；offset=1 跳 d3 拿 d2/d1
        assert [r.resource for r in rows] == ["d2", "d1"]


class TestMigration:
    def test_idempotent_multi_construct(self, pool: SqliteConnectionPool) -> None:
        """重复构造同一个 pool 上的 repo 不应失败（CREATE IF NOT EXISTS）。"""
        SqliteAuditLogRepo(pool)
        repo2 = SqliteAuditLogRepo(pool)
        repo2.record(_entry())
        assert len(repo2.list_recent()) == 1

    def test_protocol_compliance(self, repo: SqliteAuditLogRepo) -> None:
        # AuditLogPort 是 runtime_checkable Protocol
        assert isinstance(repo, AuditLogPort)
