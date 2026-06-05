"""`WebSearchPort` Fake：返回预设 WebResult 列表。"""

from __future__ import annotations

from domain.models import WebResult


class FakeWebSearch:
    def __init__(self, results: list[WebResult] | None = None) -> None:
        self._results = list(results) if results else []
        self.calls: list[tuple[str, int]] = []

    def search(self, query: str, max_results: int = 3) -> list[WebResult]:
        self.calls.append((query, max_results))
        return list(self._results[:max_results])
