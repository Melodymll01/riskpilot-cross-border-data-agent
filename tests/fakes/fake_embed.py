"""`EmbedPort` Fake：稳定返回基于文本哈希的伪向量，便于断言。"""

from __future__ import annotations

import hashlib


class FakeEmbed:
    """返回固定维度的伪向量；同一文本永远得到同一向量。"""

    def __init__(self, dim: int = 8) -> None:
        self.dim = dim
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self._hash_vector(t) for t in texts]

    def _hash_vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        # 重复扩展直到达到 dim 长度
        out: list[float] = []
        i = 0
        while len(out) < self.dim:
            out.append(digest[i % len(digest)] / 255.0)
            i += 1
        return out
