"""ApplicationReadiness 数据库与 Redis 探针测试。"""

from __future__ import annotations

from infra.health import ApplicationReadiness
from infra.storage._db import SqliteConnectionPool


class _Redis:
    def __init__(self, result: bool = True) -> None:
        self._result = result
        self.calls = 0

    def ping(self) -> bool:
        self.calls += 1
        return self._result


class _BrokenPool:
    def ping(self) -> bool:
        raise RuntimeError("database down")


def test_sqlite_ready_and_redis_disabled(tmp_path) -> None:  # type: ignore[no-untyped-def]
    readiness = ApplicationReadiness(
        database=SqliteConnectionPool(str(tmp_path / "health.sqlite3"))
    )

    assert readiness.check() == {
        "database": True,
        "redis": "disabled",
        "ready": True,
    }


def test_configured_redis_is_required(tmp_path) -> None:  # type: ignore[no-untyped-def]
    redis = _Redis(result=False)
    readiness = ApplicationReadiness(
        database=SqliteConnectionPool(str(tmp_path / "health.sqlite3")),
        redis_url="redis://example:6379/0",
        redis_client=redis,
    )

    assert readiness.check() == {
        "database": True,
        "redis": False,
        "ready": False,
    }
    assert redis.calls == 1


def test_database_failure_is_not_ready() -> None:
    readiness = ApplicationReadiness(database=_BrokenPool())

    assert readiness.check() == {
        "database": False,
        "redis": "disabled",
        "ready": False,
    }
