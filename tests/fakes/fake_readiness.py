"""Readiness fake for the required local database."""


class FakeReadiness:
    def __init__(self, *, database: bool = True) -> None:
        self._database = database
        self.calls = 0

    def check(self) -> dict[str, bool | str]:
        self.calls += 1
        return {"database": self._database, "ready": self._database}
