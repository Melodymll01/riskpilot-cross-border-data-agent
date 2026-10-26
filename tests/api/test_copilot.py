"""``/api/v2/copilot/chat`` 同步聚合端点测试。

SSE 流式端点见 ``test_copilot_sse.py``。
"""

from __future__ import annotations

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
        assert types[:3] == ["task_created", "run_started", "run_status"]
        assert types[-1] == "answer"
        assert [
            event["payload"].get("stage") for event in events if event["event_type"] == "run_status"
        ] == [
            "agent",
            "completed",
        ]
        assert "没有经过检索和校验" in events[-1]["payload"]["text"]
        assert body["run_id"].startswith("run_")

    def test_reuses_existing_task_id(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, user = authed_client
        # 先建一个 task
        from app.container import AppContainer

        container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
        task = container.task_management.create_task(user["user_id"], title="existing")

        resp = client.post(
            "/api/v2/copilot/chat",
            json={"task_id": task.task_id, "message": "继续聊"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["task_id"] == task.task_id
        # 没有 task_created 事件
        assert all(e["event_type"] != "task_created" for e in body["events"])

    def test_run_status_hides_thread_id_and_is_owner_scoped(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        response = client.post("/api/v2/copilot/chat", json={"message": "请补充哪些信息？"})
        task_id = response.json()["task_id"]
        run_id = response.json()["run_id"]

        status = client.get(f"/api/v2/copilot/runs/{run_id}")
        assert status.status_code == 200
        assert status.json()["status"] == "completed"
        assert status.json()["end_reason"] == "unverified_claim_blocked"
        assert status.json()["model_call_count"] == 1
        assert status.json()["usage_status"] == "unknown"
        assert status.json()["remaining_seconds"] <= status.json()["max_duration_seconds"]
        assert "thread_id" not in status.json()
        task = client.get(f"/api/v2/tasks/{task_id}").json()
        assert task["latest_run"]["run_id"] == run_id
        conflict = client.post(f"/api/v2/copilot/runs/{run_id}/resume")
        assert conflict.status_code == 409
        assert conflict.json()["error_code"] == "CONVERSATION_RUN_CONFLICT"

        from app.container import AppContainer

        container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
        client.cookies.set(container.settings.cookie_name, container.auth.issue_jwt("anon:other"))
        assert client.get(f"/api/v2/copilot/runs/{run_id}").status_code == 404
        assert client.post(f"/api/v2/copilot/runs/{run_id}/resume").status_code == 404

    def test_cancel_is_owner_scoped_and_idempotent_before_first_event(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, user = authed_client
        from app.container import AppContainer

        container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
        events = container.run_copilot.stream(
            owner_id=user["user_id"],
            task_id=None,
            user_message="请分析",
        )
        run_id = events.run_id  # type: ignore[attr-defined]

        first = client.post(f"/api/v2/copilot/runs/{run_id}/cancel")
        second = client.post(f"/api/v2/copilot/runs/{run_id}/cancel")
        assert first.status_code == 200
        assert first.json()["status"] == "cancelled"
        assert first.json()["end_reason"] == "user_cancelled"
        assert second.status_code == 200
        assert second.json()["status"] == "cancelled"

        client.cookies.set(container.settings.cookie_name, container.auth.issue_jwt("anon:other"))
        assert client.post(f"/api/v2/copilot/runs/{run_id}/cancel").status_code == 404


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

    def test_client_cannot_supply_thread_id(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        response = client.post(
            "/api/v2/copilot/chat",
            json={"message": "hi", "thread_id": "someone-elses-thread"},
        )
        assert response.status_code == 422


class TestChatToolLoop:
    @pytest.fixture
    def agent_script(self) -> list[AIMessage]:
        return _TOOL_THEN_FINAL

    def test_tool_call_then_answer(self, authed_client: tuple[TestClient, dict[str, Any]]) -> None:
        client, _ = authed_client
        resp = client.post(
            "/api/v2/copilot/chat",
            json={"message": "PIPL 38条说了啥"},
        )
        assert resp.status_code == 200
        types = [e["event_type"] for e in resp.json()["events"]]
        assert types[:3] == ["task_created", "run_started", "run_status"]
        assert types[-1] == "answer"
        assert "tool_call" in types
        assert "tool_result" in types


# ── Step 012-tail: Task.mode 透传 ──────────────────────────────────────
