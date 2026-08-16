"""VisualEmbedPort / VisualIndexPort Fake。"""

from __future__ import annotations

import hashlib
import math

from domain.visual import VisualAsset, VisualSearchHit


class FakeVisualEmbedder:
    def embed_images(self, images: list[bytes]) -> list[list[float]]:
        return [_vector(content) for content in images]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [_vector(text.encode("utf-8")) for text in texts]


class FakeVisualIndex:
    def __init__(self) -> None:
        self.items: dict[str, tuple[VisualAsset, list[float]]] = {}

    def add(self, asset: VisualAsset, embedding: list[float]) -> None:
        self.items[asset.asset_id] = (asset, list(embedding))

    def search(
        self,
        *,
        workspace_id: str,
        case_id: str,
        query_embedding: list[float],
        top_k: int,
    ) -> list[VisualSearchHit]:
        hits = [
            VisualSearchHit(asset=asset, score=_cosine(query_embedding, embedding))
            for asset, embedding in self.items.values()
            if asset.workspace_id == workspace_id and asset.case_id == case_id
        ]
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:top_k]

    def get(self, asset_id: str) -> VisualAsset | None:
        item = self.items.get(asset_id)
        return item[0] if item else None


def _vector(content: bytes) -> list[float]:
    digest = hashlib.sha256(content).digest()
    values = [digest[index] / 255.0 for index in range(8)]
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return [value / norm for value in values]


def _cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))
