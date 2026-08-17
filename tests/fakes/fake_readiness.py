"""ReadinessPort Fake。"""

from __future__ import annotations


class FakeReadiness:
    def __init__(
        self,
        *,
        database: bool = True,
        redis: bool | str = "disabled",
    ) -> None:
        self._database = database
        self._redis = redis
        self.calls = 0

    def check(self) -> dict[str, bool | str]:
        self.calls += 1
        ready = self._database and (self._redis is True or self._redis == "disabled")
        return {
            "database": self._database,
            "redis": self._redis,
            "ready": ready,
        }
