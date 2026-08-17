"""必需基础设施 readiness 适配器。"""

from __future__ import annotations

from typing import Any, Protocol


class _SqlitePoolLike(Protocol):
    def get(self) -> Any: ...


class ApplicationReadiness:
    """检查业务数据库；Redis 配置后同时检查 Redis。"""

    def __init__(
        self,
        *,
        sqlite_pool: _SqlitePoolLike | None,
        redis_url: str | None = None,
        redis_client: Any | None = None,
    ) -> None:
        self._sqlite_pool = sqlite_pool
        self._redis_url = redis_url
        self._redis_client = redis_client

    def check(self) -> dict[str, bool | str]:
        checks: dict[str, bool | str] = {
            "database": self._check_database(),
            "redis": self._check_redis(),
        }
        checks["ready"] = all(
            value is True or value == "disabled" for key, value in checks.items() if key != "ready"
        )
        return checks

    def _check_database(self) -> bool:
        if self._sqlite_pool is None:
            return False
        try:
            row = self._sqlite_pool.get().execute("SELECT 1").fetchone()
        except Exception:
            return False
        return row is not None and row[0] == 1

    def _check_redis(self) -> bool | str:
        if not self._redis_url:
            return "disabled"
        try:
            client = self._redis_client or self._build_redis_client()
            return bool(client.ping())
        except Exception:
            return False

    def _build_redis_client(self) -> Any:
        try:
            import redis
        except ImportError as exc:
            raise RuntimeError("配置 REDIS_URL 后必须安装 redis 依赖") from exc
        return redis.Redis.from_url(
            self._redis_url,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
