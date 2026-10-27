"""``build_reranker`` 工厂的选择逻辑（无模型加载）。

Step 027：把 v1 ``KnowledgeService`` 内联的 reranker 选择逻辑提取为工厂，
供 v2 ``HybridRetrieverAdapter`` 懒加载注入。
"""

from __future__ import annotations

import pytest

import retrieval.search.reranker as reranker_mod
from retrieval.search.reranker import (
    DistanceThresholdReranker,
    build_reranker,
)


class _FakeCrossEncoder:
    """假 CrossEncoderReranker：不加载模型。"""

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs

    def rerank(self, query: str, results: list) -> list:  # pragma: no cover - 不被调用
        return results


def test_disabled_returns_distance_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reranker_mod.settings, "enable_reranker", False)
    r = build_reranker()
    assert isinstance(r, DistanceThresholdReranker)


def test_enabled_returns_cross_encoder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reranker_mod.settings, "enable_reranker", True)
    monkeypatch.setattr(reranker_mod, "CrossEncoderReranker", _FakeCrossEncoder)
    r = build_reranker()
    assert isinstance(r, _FakeCrossEncoder)


def test_enabled_passes_settings_to_cross_encoder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reranker_mod.settings, "enable_reranker", True)
    monkeypatch.setattr(reranker_mod.settings, "reranker_model", "test/model")
    monkeypatch.setattr(reranker_mod.settings, "reranker_device", "cpu")
    monkeypatch.setattr(reranker_mod.settings, "reranker_score_threshold", 0.5)
    monkeypatch.setattr(reranker_mod, "CrossEncoderReranker", _FakeCrossEncoder)
    r = build_reranker()
    assert isinstance(r, _FakeCrossEncoder)
    assert r.kwargs == {
        "model_name": "test/model",
        "device": "cpu",
        "score_threshold": 0.5,
    }


def test_cross_encoder_load_failure_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reranker_mod.settings, "enable_reranker", True)

    def _boom(**kwargs: object) -> None:
        raise RuntimeError("模型加载失败：无网络")

    monkeypatch.setattr(reranker_mod, "CrossEncoderReranker", _boom)
    r = build_reranker()
    assert isinstance(r, DistanceThresholdReranker)
