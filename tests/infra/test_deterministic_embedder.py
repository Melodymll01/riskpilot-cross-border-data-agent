"""确定性 Embedding Adapter 协议测试。"""

from __future__ import annotations

import math

import pytest

from domain import EmbedPort
from infra.search import DeterministicEmbedder


def test_deterministic_embedder_is_stable_and_normalized() -> None:
    embedder = DeterministicEmbedder(8)

    first = embedder.embed(["重要数据", "个人信息"])
    second = embedder.embed(["重要数据"])

    assert isinstance(embedder, EmbedPort)
    assert first[0] == second[0]
    assert first[0] != first[1]
    assert len(first[0]) == 8
    assert math.sqrt(sum(value * value for value in first[0])) == pytest.approx(1.0)
