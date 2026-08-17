"""``/api/v2/copilot/chat/stream`` SSE 流式端点测试。

策略：用 TestClient 的 stream() 读 ``text/event-stream``，按 ``\\n\\n`` 切帧。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

_TOOL_THEN_FINAL = [
    AIMessage(
        content="",
        tool_calls=[
            {
                "name": "search_law",
                "args": {"query": "PIPL"},
                "id": "call_search_law",
                "type": "tool_call",
            }
        ],
    ),
    AIMessage(content="答"),
]


def _read_sse_frames(text: str) -> list[dict[str, Any]]:
    """把 SSE 响应文本切成 [{event, data}]；忽略心跳/空帧。"""
    out: list[dict[str, Any]] = []
    for raw in text.split("\n\n"):
        block = raw.strip()
        if not block or block.startswith(":"):
            continue
        event_type = ""
        data = ""
        for line in block.splitlines():
            if line.startswith("event:"):
                event_type = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data = line[len("data:") :].strip()
        out.append({"event": event_type, "data": json.loads(data) if data else None})
    return out


class TestSseSimple:
    @pytest.fixture
    def agent_script(self) -> list[AIMessage]:
        return [AIMessage(content="done")]

    def test_stream_emits_sse_frames(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        with client.stream(
            "POST",
            "/api/v2/copilot/chat/stream",
            json={"message": "hi"},
        ) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            body = resp.read().decode("utf-8")

        frames = _read_sse_frames(body)
        events = [f["event"] for f in frames]
        assert "task_created" in events
        assert events[-1] == "answer"
        answer = next(f for f in frames if f["event"] == "answer")
        assert answer["data"]["text"] == "done"


class TestSseToolLoop:
    @pytest.fixture
    def agent_script(self) -> list[AIMessage]:
        return _TOOL_THEN_FINAL

    def test_stream_includes_tool_call_and_result(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        with client.stream(
            "POST",
            "/api/v2/copilot/chat/stream",
            json={"message": "PIPL"},
        ) as resp:
            assert resp.status_code == 200
            body = resp.read().decode("utf-8")

        frames = _read_sse_frames(body)
        events = [f["event"] for f in frames]
        assert "tool_call" in events
        assert "tool_result" in events
        tc = next(f for f in frames if f["event"] == "tool_call")
        assert tc["data"]["tool_name"] == "search_law"


class TestSseAuth:
    def test_stream_requires_auth(self, client: TestClient) -> None:
        resp = client.post("/api/v2/copilot/chat/stream", json={"message": "hi"})
        assert resp.status_code == 401
