"""``ChatClient.complete`` 的 response_format 透传与降级行为（无网络）。

Step 026e：决策轮用 ``response_format={"type": "json_object"}`` 在模型层强制合法
JSON；某些网关不支持该参数时需自动降级重试一次。
"""

from __future__ import annotations

import pytest
from openai import BadRequestError

from retrieval.generation.chat_client import ChatClient


class _Msg:
    def __init__(self, content: str) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str) -> None:
        self.message = _Msg(content)


class _Resp:
    def __init__(self, content: str) -> None:
        self.choices = [_Choice(content)]


class _Completions:
    def __init__(self, fail_with_format: bool = False) -> None:
        self.fail_with_format = fail_with_format
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail_with_format and "response_format" in kwargs:
            # 模拟不支持 response_format 的网关
            raise BadRequestError.__new__(BadRequestError)
        return _Resp("ok")


class _Chat:
    def __init__(self, fail_with_format: bool = False) -> None:
        self.completions = _Completions(fail_with_format)


class _StubOpenAI:
    def __init__(self, fail_with_format: bool = False) -> None:
        self.chat = _Chat(fail_with_format)


def _make_client(stub: _StubOpenAI) -> ChatClient:
    client = ChatClient.__new__(ChatClient)
    client.provider = "openai"
    client.client = stub
    client.model = "test-model"
    return client


def test_response_format_forwarded() -> None:
    stub = _StubOpenAI()
    client = _make_client(stub)
    out = client.complete([{"role": "user", "content": "hi"}], response_format={"type": "json_object"})
    assert out == "ok"
    assert stub.chat.completions.calls[0]["response_format"] == {"type": "json_object"}


def test_response_format_omitted_by_default() -> None:
    stub = _StubOpenAI()
    client = _make_client(stub)
    client.complete([{"role": "user", "content": "hi"}])
    assert "response_format" not in stub.chat.completions.calls[0]


def test_bad_request_degrades_and_retries() -> None:
    stub = _StubOpenAI(fail_with_format=True)
    client = _make_client(stub)
    out = client.complete([{"role": "user", "content": "hi"}], response_format={"type": "json_object"})
    assert out == "ok"
    # 第一次带 response_format 失败，第二次去掉重试成功
    assert len(stub.chat.completions.calls) == 2
    assert "response_format" in stub.chat.completions.calls[0]
    assert "response_format" not in stub.chat.completions.calls[1]


def test_bad_request_without_format_propagates() -> None:
    stub = _StubOpenAI(fail_with_format=True)
    # fail_with_format only triggers when response_format present; force always-fail
    def _always_fail(**kwargs):
        raise BadRequestError.__new__(BadRequestError)

    stub.chat.completions.create = _always_fail  # type: ignore[method-assign]
    client = _make_client(stub)
    with pytest.raises(BadRequestError):
        client.complete([{"role": "user", "content": "hi"}])
