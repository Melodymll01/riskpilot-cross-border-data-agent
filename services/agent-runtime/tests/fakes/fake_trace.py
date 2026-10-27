"""TracePort Fake。"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any, Literal


class FakeTraceSpan:
    def __init__(self, record: dict[str, Any]) -> None:
        self._record = record

    def add_metadata(self, metadata: Mapping[str, Any]) -> None:
        self._record["metadata"].update(metadata)


class FakeTrace:
    def __init__(self) -> None:
        self.spans: list[dict[str, Any]] = []

    @contextmanager
    def span(
        self,
        name: str,
        *,
        run_type: Literal["chain", "llm", "tool", "retriever"] = "chain",
        metadata: Mapping[str, Any] | None = None,
    ) -> Iterator[FakeTraceSpan]:
        record = {
            "name": name,
            "run_type": run_type,
            "metadata": dict(metadata or {}),
        }
        self.spans.append(record)
        yield FakeTraceSpan(record)
