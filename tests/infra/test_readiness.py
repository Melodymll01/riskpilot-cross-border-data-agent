"""ApplicationReadiness 数据库与 Redis 探针测试。"""

from __future__ import annotations

from infra.health import ApplicationReadiness
from infra.storage._db import SqliteConnectionPool


class _BrokenPool:
    def ping(self) -> bool:
        raise RuntimeError("database down")


def test_sqlite_ready(tmp_path) -> None:  # type: ignore[no-untyped-def]
    readiness = ApplicationReadiness(
        database=SqliteConnectionPool(str(tmp_path / "health.sqlite3"))
    )

    assert readiness.check() == {
        "database": True,
        "ready": True,
    }


def test_database_failure_is_not_ready() -> None:
    readiness = ApplicationReadiness(database=_BrokenPool())

    assert readiness.check() == {
        "database": False,
        "ready": False,
    }
