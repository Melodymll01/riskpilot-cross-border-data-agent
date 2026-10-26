"""Exercise both model transports without remote credentials or inference calls."""

from __future__ import annotations

import json

import httpx
import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from app.factories import build_agent_model, build_chat, build_retriever
from config import RuntimeConfigurationError, Settings
from infra.chat import build_chat_model
from infra.chat.runtime import model_control_context
from infra.chat.traex_model import TraexChatModel
from retrieval.search.query_rewriter import QueryRewriter
from tests.fakes.fake_agent_model import FakeToolCallingModel
from tests.infra.test_service_adapters import _RecordingRunControl
from tests.infra.test_traex_model import _cli, _events


def _settings(**overrides):
    return Settings(
        _env_file=None,
        **{
            "llm_provider": "api",
            "embed_provider": "sentence_transformer",
            "openai_api_key": "test-shared-key",
            "openai_api_base": "https://shared.example/v1",
            "chat_api_key": "test-chat-key",
            "chat_api_base": "https://chat.example/v1",
            "chat_model": "test-model",
            "local_chat_model": "test-local-model",
            "ollama_api_base": "http://localhost:11434/v1",
            "enable_query_rewrite": True,
            **overrides,
        },
    )


@tool
def search_law(query: str) -> str:
    """Retrieve relevant regulations."""
    raise AssertionError("Model transports must only propose application tools")


@pytest.mark.parametrize(
    "overrides,host,key,model_name",
    [
        ({}, "chat.example", "test-chat-key", "test-model"),
        (
            {"chat_api_key": None, "chat_api_base": None},
            "shared.example",
            "test-shared-key",
            "test-model",
        ),
        ({"llm_provider": "local"}, "localhost", "ollama", "test-local-model"),
    ],
)
def test_api_key_json_stream_and_tool_protocol(monkeypatch, overrides, host, key, model_name):
    from infra.chat import openai_model

    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        assert request.url.host == host
        assert request.headers["authorization"] == "Bearer " + key
        assert body["model"] == model_name
        assert body["stream"] is True
        assert "runtime_control" not in body
        if body.get("tools"):
            delta = {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call-law",
                        "type": "function",
                        "function": {"name": "search_law", "arguments": '{"query":"数据出境"}'},
                    }
                ]
            }
            finish = "tool_calls"
        else:
            assert body["response_format"] == {"type": "json_object"}
            assert body["max_completion_tokens"] == 128
            delta = {"content": '{"ok":true}'}
            finish = "stop"
        delta["role"] = "assistant"
        events = [
            {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
            {
                "choices": [],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
            },
        ]
        wire = (
            "".join(
                "data: "
                + json.dumps(
                    {
                        "id": "chat-test",
                        "object": "chat.completion.chunk",
                        "created": 1,
                        "model": model_name,
                        **event,
                    }
                )
                + "\n\n"
                for event in events
            )
            + "data: [DONE]\n\n"
        )
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, text=wire)

    original = openai_model.ChatOpenAI
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(
            openai_model, "ChatOpenAI", lambda **kwargs: original(http_client=client, **kwargs)
        )
        config = _settings(**overrides)
        control = _RecordingRunControl()
        response = build_chat(config).chat_with_usage(
            [{"role": "user", "content": "返回 JSON"}],
            json_mode=True,
            max_tokens=128,
            runtime_control=control,
            operation="verification",
        )
        assert json.loads(response.content) == {"ok": True}
        assert response.usage_available and response.token_usage == 13
        assert control.calls == [("verification", "verification")]
        assert control.usage == [(10, 3)]
        answer = build_agent_model(config).bind_tools([search_law]).invoke("查法规")
        assert answer.tool_calls[0]["args"] == {"query": "数据出境"}
        assert len(requests) == 2


def test_traex_json_and_tools_ignore_api_credentials(tmp_path, monkeypatch):
    from infra.chat import openai_model

    def forbid_api(**kwargs):
        raise AssertionError("Traex mode must not construct an API client")

    monkeypatch.setattr(openai_model, "ChatOpenAI", forbid_api)
    text_events = _events('{"ok":true}')
    tool_events = _events("", [{"name": "search_law", "arguments_json": '{"query":"数据出境"}'}])
    path = _cli(
        tmp_path,
        "import json,sys\nrequest=json.load(sys.stdin)\n"
        "assert 'test-chat-key' not in json.dumps(request)\n"
        "assert sys.argv[sys.argv.index('--model')+1] == 'gpt-5.4'\n"
        "if request['available_application_tools']:\n"
        f"    print({tool_events!r})\n"
        "else:\n"
        "    assert request['response_format'] == {'type': 'json_object'}\n"
        "    assert request['requested_output_token_limit'] == 128\n"
        f"    print({text_events!r})\n",
    )
    config = _settings(llm_provider="traex", traex_bin=path, traex_model="gpt-5.4")
    response = build_chat(config).chat_with_usage(
        [{"role": "user", "content": "返回 JSON"}],
        json_mode=True,
        max_tokens=128,
    )
    assert json.loads(response.content) == {"ok": True}
    assert response.token_usage == 13
    answer = build_agent_model(config).bind_tools([search_law]).invoke("查法规")
    assert answer.tool_calls[0]["args"] == {"query": "数据出境"}
    assert answer.response_metadata["cli"] == "traex"
    assert answer.response_metadata["provider"] == "trae"
    assert answer.response_metadata["requested_output_token_limit"] == config.chat_max_tokens
    assert answer.response_metadata["sampling_parameters_enforced"] is False


def test_qwen_json_calls_disable_thinking(monkeypatch):
    import app.factories as factories

    model = FakeToolCallingModel(responses=[AIMessage(content="{}")])
    monkeypatch.setattr(factories, "build_chat_model", lambda _settings: model)

    chat = factories.build_chat(_settings(chat_model="qwen3.5-plus"))
    chat.chat([{"role": "user", "content": "返回 JSON"}], json_mode=True)

    assert model.generation_kwargs[0]["extra_body"] == {"enable_thinking": False}


def test_query_rewrite_uses_traex_and_records_budget_once(tmp_path, monkeypatch):
    from infra.chat import openai_model

    monkeypatch.setattr(openai_model, "ChatOpenAI", lambda **kw: pytest.fail("API route used"))
    path = _cli(tmp_path, f"import sys\nsys.stdin.read()\nprint({_events('跨境数据传输条件')!r})\n")
    config = _settings(llm_provider="traex", traex_bin=path)

    class Control(_RecordingRunControl):
        def __init__(self):
            super().__init__()
            self.checks = 0

        def check(self):
            self.checks += 1

    control = Control()
    rewriter = QueryRewriter(config=config)
    assert rewriter.rewrite("传输条件", runtime_control=control) == ["传输条件", "跨境数据传输条件"]
    assert isinstance(rewriter._model, TraexChatModel)
    assert control.calls == [("query_rewrite", "tool")]
    assert control.usage == [(10, 3)]
    assert control.checks > 0
    rewriter.rewrite("传输条件", runtime_control=control)
    assert len(control.calls) == 1


def test_retrieval_factory_passes_its_model_settings(monkeypatch):
    from retrieval.search import embedder, reranker, retriever, vector_store

    monkeypatch.setattr(embedder, "Embedder", lambda **kw: object())
    monkeypatch.setattr(vector_store, "VectorStore", lambda **kw: object())
    monkeypatch.setattr(reranker, "build_reranker", lambda **kw: None)
    captured = {}

    def capture(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(retriever, "Retriever", capture)
    config = _settings(llm_provider="traex")
    build_retriever(config)._ensure_retriever()
    assert captured["query_rewriter"]._settings is config


def test_disabled_rewrite_does_not_require_model_credentials():
    rewriter = QueryRewriter(
        config=_settings(
            enable_query_rewrite=False,
            openai_api_key="",
            chat_api_key="",
        )
    )
    assert rewriter.rewrite("数据出境") == ["数据出境"]
    assert rewriter._model is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"openai_api_key": "", "chat_api_key": ""},
        {"chat_api_base": "not-a-url"},
        {"chat_model": " "},
        {"llm_provider": "traex", "traex_bin": "/opt/homebrew/bin/codex"},
        {"llm_provider": "traex", "traex_model": ""},
    ],
)
def test_invalid_selected_model_config_fails_before_transport(overrides):
    with pytest.raises(RuntimeConfigurationError):
        build_chat_model(_settings(**overrides))


def test_traex_needs_no_api_key_and_api_needs_no_traex_installation():
    traex = build_chat_model(_settings(llm_provider="traex", openai_api_key="", chat_api_key=""))
    assert isinstance(traex, TraexChatModel)
    api = build_chat_model(_settings(traex_bin="/not-installed/traex"))
    assert api.model_name == "test-model"


def test_agent_model_context_cancellation_reaches_traex_before_spawn(monkeypatch):
    class Cancelled(Exception):
        pass

    class Control:
        def check(self):
            raise Cancelled()

    monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: pytest.fail("process started"))
    with model_control_context(Control()), pytest.raises(Cancelled):
        build_agent_model(_settings(llm_provider="traex")).invoke("取消")
