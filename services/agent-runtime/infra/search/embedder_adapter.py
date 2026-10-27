"""`EmbedPort` 适配器：包装 retrieval/search/embedder.Embedder。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from domain.ports import RunControlPort


class _EmbedderLike(Protocol):
    def embed_texts(
        self,
        texts: list[str],
        *,
        runtime_control: RunControlPort | None = None,
    ) -> list[list[float]]: ...


class EmbedderAdapter:
    """实现 `EmbedPort`，委托给现有 `Embedder.embed_texts`。"""

    def __init__(self, embedder: _EmbedderLike | None = None) -> None:
        if embedder is None:
            from retrieval.search.embedder import Embedder

            embedder = Embedder()
        self._embedder = embedder

    def embed(
        self,
        texts: list[str],
        *,
        runtime_control: RunControlPort | None = None,
    ) -> list[list[float]]:
        if runtime_control is None:
            return self._embedder.embed_texts(texts)
        return self._embedder.embed_texts(texts, runtime_control=runtime_control)
