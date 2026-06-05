"""`RetrievePort` Fake：返回预设 Chunk 列表。"""

from __future__ import annotations

from domain.models import Chunk, Corpus


class FakeRetrieve:
    def __init__(self, chunks: list[Chunk] | None = None) -> None:
        self._chunks = list(chunks) if chunks else []
        self.calls: list[dict] = []

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        corpus: Corpus = "law",
        owner_id: str | None = None,
        filters: dict[str, str] | None = None,
    ) -> list[Chunk]:
        self.calls.append(
            {
                "query": query,
                "top_k": top_k,
                "corpus": corpus,
                "owner_id": owner_id,
                "filters": filters,
            }
        )
        return list(self._chunks[:top_k])
