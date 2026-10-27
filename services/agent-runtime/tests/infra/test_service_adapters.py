"""服务适配器测试：Chat / Embed / WebSearch。"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from langchain_core.messages import AIMessage

from domain.models import WebResult
from domain.ports import ChatPort, EmbedPort, WebSearchPort
from infra.chat import LangChainChatAdapter
from infra.search import EmbedderAdapter
from infra.web import DuckDuckGoAdapter
from tests.fakes.fake_agent_model import FakeToolCallingModel

# ── Stub 客户端（无网络） ──────────────────────────────────────────────


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


class _RecordingRunControl:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.usage: list[tuple[int | None, int | None]] = []

    def call_model(self, *, operation, stage, invoke, retry=False):
        assert retry is False
        self.calls.append((operation, stage))
        return invoke()

    def record_model_usage(self, *, input_tokens, output_tokens) -> None:
        self.usage.append((input_tokens, output_tokens))


# ── Chat ──────────────────────────────────────────────────────────────


class TestLangChainChatAdapter:
    def test_implements_chat_port(self) -> None:
        adapter = LangChainChatAdapter(
            FakeToolCallingModel(responses=[AIMessage(content="stub-answer")])
        )
        assert isinstance(adapter, ChatPort)

    def test_chat_delegates_with_kwargs(self) -> None:
        model = FakeToolCallingModel(responses=[AIMessage(content="hi")])
        adapter = LangChainChatAdapter(model)
        out = adapter.chat(
            [{"role": "user", "content": "你好"}],
            temperature=0.5,
            max_tokens=128,
        )
        assert out == "hi"
        assert model.calls[0][0].content == "你好"
        assert model.generation_kwargs[0]["temperature"] == 0.5
        assert model.generation_kwargs[0]["max_completion_tokens"] == 128
        assert "response_format" not in model.generation_kwargs[0]

    def test_chat_json_mode_forwards_response_format(self) -> None:
        model = FakeToolCallingModel(responses=[AIMessage(content="{}")])
        adapter = LangChainChatAdapter(
            model,
            json_extra_body={"enable_thinking": False},
        )
        adapter.chat([{"role": "user", "content": "hi"}], json_mode=True)
        assert model.generation_kwargs[0]["response_format"] == {"type": "json_object"}
        assert model.generation_kwargs[0]["extra_body"] == {"enable_thinking": False}

    def test_length_finish_reason_is_translated_to_safe_runtime_error(self, monkeypatch) -> None:
        from infra.chat import langchain_chat

        class FakeLengthFinishReasonError(Exception):
            pass

        class TruncatedModel:
            def invoke(self, *_args, **_kwargs):
                raise FakeLengthFinishReasonError

        monkeypatch.setattr(
            langchain_chat,
            "LengthFinishReasonError",
            FakeLengthFinishReasonError,
        )
        adapter = LangChainChatAdapter(TruncatedModel())  # type: ignore[arg-type]
        with pytest.raises(RuntimeError, match="长度上限"):
            adapter.chat([{"role": "user", "content": "hi"}], json_mode=True)

    def test_chat_records_only_provider_reported_usage_in_run_control(self) -> None:
        model = FakeToolCallingModel(
            responses=[
                AIMessage(
                    content="{}",
                    usage_metadata={
                        "input_tokens": 9,
                        "output_tokens": 4,
                        "total_tokens": 13,
                    },
                )
            ]
        )
        control = _RecordingRunControl()

        response = LangChainChatAdapter(model).chat_with_usage(
            [{"role": "user", "content": "hi"}],
            json_mode=True,
            runtime_control=control,  # type: ignore[arg-type]
            operation="verification",
        )

        assert response.usage_available is True
        assert control.calls == [("verification", "verification")]
        assert control.usage == [(9, 4)]


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
