"""服务适配器测试：Chat / Embed / WebSearch / Evidence。"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from domain.models import EvidenceJudgement, WebResult
from domain.ports import ChatPort, EmbedPort, EvidencePort, WebSearchPort
from infra.chat import OpenAIChatAdapter
from infra.evidence import MockEvidenceClient
from infra.search import EmbedderAdapter
from infra.web import DuckDuckGoAdapter

# ── Stub 客户端（无网络） ──────────────────────────────────────────────


class _StubChatClient:
    def __init__(self, response: str = "stub-answer") -> None:
        self.response = response
        self.calls: list[dict] = []

    def complete(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        return self.response


class _StubEmbedder:
    def __init__(self, dim: int = 4) -> None:
        self.dim = dim
        self.calls: list[list[str]] = []

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[0.1] * self.dim for _ in texts]


@dataclass
class _StubRawWebResult:
    title: str
    url: str
    snippet: str


class _StubSearcher:
    def __init__(self, results: list[_StubRawWebResult] | None = None) -> None:
        self.results = list(results) if results else []
        self.calls: list[tuple[str, int]] = []

    def search(self, query: str, max_results: int = 3) -> list[_StubRawWebResult]:
        self.calls.append((query, max_results))
        return self.results[:max_results]


# ── Chat ──────────────────────────────────────────────────────────────


class TestOpenAIChatAdapter:
    def test_implements_chat_port(self) -> None:
        adapter = OpenAIChatAdapter(client=_StubChatClient())
        assert isinstance(adapter, ChatPort)

    def test_chat_delegates_with_kwargs(self) -> None:
        client = _StubChatClient(response="hi")
        adapter = OpenAIChatAdapter(client=client)
        out = adapter.chat(
            [{"role": "user", "content": "你好"}],
            temperature=0.5,
            max_tokens=128,
        )
        assert out == "hi"
        assert client.calls[0]["temperature"] == 0.5
        assert client.calls[0]["max_tokens"] == 128
        assert client.calls[0]["messages"] == [{"role": "user", "content": "你好"}]


# ── Embed ─────────────────────────────────────────────────────────────


class TestEmbedderAdapter:
    def test_implements_embed_port(self) -> None:
        adapter = EmbedderAdapter(embedder=_StubEmbedder())
        assert isinstance(adapter, EmbedPort)

    def test_embed_delegates(self) -> None:
        stub = _StubEmbedder(dim=3)
        adapter = EmbedderAdapter(embedder=stub)
        result = adapter.embed(["a", "bb"])
        assert result == [[0.1, 0.1, 0.1], [0.1, 0.1, 0.1]]
        assert stub.calls == [["a", "bb"]]


# ── WebSearch ─────────────────────────────────────────────────────────


class TestDuckDuckGoAdapter:
    def test_implements_web_search_port(self) -> None:
        adapter = DuckDuckGoAdapter(searcher=_StubSearcher())
        assert isinstance(adapter, WebSearchPort)

    def test_search_converts_to_web_result(self) -> None:
        stub = _StubSearcher(
            results=[
                _StubRawWebResult(title="t1", url="https://a.com", snippet="s1"),
                _StubRawWebResult(title="t2", url="https://b.com", snippet="s2"),
            ]
        )
        adapter = DuckDuckGoAdapter(searcher=stub)
        results = adapter.search("query", max_results=5)
        assert len(results) == 2
        assert isinstance(results[0], WebResult)
        assert results[0].url == "https://a.com"
        assert stub.calls == [("query", 5)]

    def test_search_skips_empty_url(self) -> None:
        stub = _StubSearcher(
            results=[
                _StubRawWebResult(title="t1", url="", snippet="s"),
                _StubRawWebResult(title="t2", url="https://b.com", snippet="s2"),
            ]
        )
        adapter = DuckDuckGoAdapter(searcher=stub)
        results = adapter.search("q")
        assert [r.url for r in results] == ["https://b.com"]


# ── Evidence ──────────────────────────────────────────────────────────


class TestMockEvidenceClient:
    def test_implements_evidence_port(self) -> None:
        assert isinstance(MockEvidenceClient(), EvidencePort)

    def test_judge_returns_stable_label(self) -> None:
        client = MockEvidenceClient()
        j1 = client.judge("F1", {"region": "EU"})
        j2 = client.judge("F1", {"region": "US"})
        assert isinstance(j1, EvidenceJudgement)
        assert j1.factor_id == "F1"
        # 同一 factor_id 应得到相同 label（确定性）
        assert j1.label == j2.label
        assert j1.label in ("low", "moderate", "high")
        assert 0.0 <= j1.confidence <= 1.0

    def test_judge_rationale_includes_context_keys(self) -> None:
        client = MockEvidenceClient()
        j = client.judge("F2", {"region": "EU", "data_type": "PI"})
        assert "data_type" in j.rationale
        assert "region" in j.rationale

    def test_invalid_confidence_rejected(self) -> None:
        with pytest.raises(ValueError):
            MockEvidenceClient(default_confidence=1.5)
