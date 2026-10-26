"""Readiness for the persistent local database."""

from __future__ import annotations

from typing import Protocol


class _DatabaseProbe(Protocol):
    def ping(self) -> bool: ...


class ApplicationReadiness:
    def __init__(self, *, database: _DatabaseProbe | None) -> None:
        self._database = database

    def check(self) -> dict[str, bool | str]:
        try:
            ready = self._database is not None and self._database.ping()
        except Exception:
            ready = False
        return {"database": ready, "ready": ready}
