"""``/api/v2/copilot/chat`` 同步聚合端点测试。

SSE 流式端点见 ``test_copilot_sse.py``。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from tests.fakes.fake_research import FakeResearch

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
    AIMessage(content="依据 [PIPL] 第38条……"),
]


class TestChatSyncSimple:
    @pytest.fixture
    def agent_script(self) -> list[AIMessage]:
        return [AIMessage(content="回答完毕")]

    def test_creates_task_and_returns_events(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        resp = client.post(
            "/api/v2/copilot/chat",
            json={"message": "请问PIPL是什么？"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["task_id"].startswith("task_")
        events = body["events"]
        types = [e["event_type"] for e in events]
        assert types == ["task_created", "answer"]
        assert events[-1]["payload"]["text"] == "回答完毕"

    def test_reuses_existing_task_id(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, user = authed_client
        # 先建一个 task
        from app.container import AppContainer

        container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
        task = container.task_management.create_task(
            user["user_id"], title="existing"
        )

        resp = client.post(
            "/api/v2/copilot/chat",
            json={"task_id": task.task_id, "message": "继续聊"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["task_id"] == task.task_id
        # 没有 task_created 事件
        assert all(e["event_type"] != "task_created" for e in body["events"])


class TestChatRequiresAuth:
    def test_unauthed_returns_401(self, client: TestClient) -> None:
        resp = client.post("/api/v2/copilot/chat", json={"message": "hi"})
        assert resp.status_code == 401


class TestChatValidation:
    def test_empty_message_returns_422(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        resp = client.post("/api/v2/copilot/chat", json={"message": ""})
        assert resp.status_code == 422

    def test_too_many_attachments_returns_422(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        resp = client.post(
            "/api/v2/copilot/chat",
            json={
                "message": "hi",
                "attachment_doc_ids": [f"DOC-{i}" for i in range(25)],
            },
        )
        assert resp.status_code == 422


class TestChatToolLoop:
    @pytest.fixture
    def agent_script(self) -> list[AIMessage]:
        return _TOOL_THEN_FINAL

    def test_tool_call_then_answer(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        resp = client.post(
            "/api/v2/copilot/chat",
            json={"message": "PIPL 38条说了啥"},
        )
        assert resp.status_code == 200
        types = [e["event_type"] for e in resp.json()["events"]]
        assert types == [
            "task_created",
            "tool_call",
            "tool_result",
            "answer",
        ]


# ── Step 012-tail: Task.mode 透传 ──────────────────────────────────────


class TestChatMode:
    """ChatRequest.mode 应在新建 Task 时被持久化。"""

    @pytest.fixture
    def agent_script(self) -> list[AIMessage]:
        return [AIMessage(content="回答完毕")]

    def test_default_mode_is_qa(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        resp = client.post(
            "/api/v2/copilot/chat",
            json={"message": "什么是个保法"},
        )
        assert resp.status_code == 200
        task_id = resp.json()["task_id"]
        detail = client.get(f"/api/v2/tasks/{task_id}").json()
        assert detail["task"]["mode"] == "qa"

    def test_explicit_research_mode_persisted(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, user = authed_client
        resp = client.post(
            "/api/v2/copilot/chat",
            json={"message": "分析中美数据出境监管差异", "mode": "research"},
        )
        assert resp.status_code == 200
        task_id = resp.json()["task_id"]
        detail = client.get(f"/api/v2/tasks/{task_id}").json()
        assert detail["task"]["mode"] == "research"
        from app.container import AppContainer

        container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
        assert isinstance(container.research, FakeResearch)
        assert container.research.calls == [
            {
                "query": "分析中美数据出境监管差异",
                "owner_id": user["user_id"],
                "top_k": 8,
                "enable_web_search": True,
            }
        ]

    def test_explicit_profile_mode_persisted(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        resp = client.post(
            "/api/v2/copilot/chat",
            json={"message": "我司想做风险画像", "mode": "profile"},
        )
        assert resp.status_code == 200
        task_id = resp.json()["task_id"]
        detail = client.get(f"/api/v2/tasks/{task_id}").json()
        assert detail["task"]["mode"] == "profile"

    def test_invalid_mode_returns_422(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        resp = client.post(
            "/api/v2/copilot/chat",
            json={"message": "x", "mode": "weather"},
        )
        assert resp.status_code == 422
