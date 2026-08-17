"""离线测试与 Seed Demo 使用的确定性 Embedding Adapter。"""

from __future__ import annotations

import hashlib
import math


class DeterministicEmbedder:
    """基于 SHA-256 生成稳定向量；只验证协议，不代表真实语义效果。"""

    def __init__(self, dimensions: int) -> None:
        if dimensions < 1:
            raise ValueError("dimensions 必须大于 0")
        self._dimensions = dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        seed = hashlib.sha256(text.encode("utf-8")).digest()
        values = [(seed[index % len(seed)] / 127.5) - 1.0 for index in range(self._dimensions)]
        norm = math.sqrt(sum(value * value for value in values))
        return [value / norm for value in values] if norm else values
