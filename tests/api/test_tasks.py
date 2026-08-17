"""``/api/v2/tasks/*`` 路由测试。"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.container import AppContainer


def _seed_task(client: TestClient, owner_id: str, *, title: str = "t", goal: str = "") -> str:
    container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
    task = container.task_management.create_task(owner_id, title=title, user_goal=goal)
    return task.task_id


class TestListTasks:
    def test_empty_owner(self, authed_client: tuple[TestClient, dict[str, Any]]) -> None:
        client, _ = authed_client
        resp = client.get("/api/v2/tasks")
        assert resp.status_code == 200
        assert resp.json() == {"tasks": []}

    def test_lists_own_tasks_only(self, authed_client: tuple[TestClient, dict[str, Any]]) -> None:
        client, user = authed_client
        owner_id = user["user_id"]
        # 我自己 2 个
        for i in range(2):
            _seed_task(client, owner_id, title=f"mine-{i}")
        # 别人的 1 个（直接通过 container 注入，不走 HTTP）
        container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
        container.task_management.create_task("anon:other", title="other-task")

        resp = client.get("/api/v2/tasks")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["tasks"]) == 2
        assert all(t["owner_id"] == owner_id for t in body["tasks"])
        assert {t["title"] for t in body["tasks"]} == {"mine-0", "mine-1"}

    def test_limit_param_clamped(self, authed_client: tuple[TestClient, dict[str, Any]]) -> None:
        client, user = authed_client
        resp = client.get("/api/v2/tasks?limit=5000")
        assert resp.status_code == 200  # 不报错，内部夹紧到 200


class TestGetTask:
    def test_owner_can_get_own_task(self, authed_client: tuple[TestClient, dict[str, Any]]) -> None:
        client, user = authed_client
        tid = _seed_task(client, user["user_id"], title="hello")
        resp = client.get(f"/api/v2/tasks/{tid}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["task"]["task_id"] == tid
        assert body["task"]["title"] == "hello"
        assert body["messages"] == []

    def test_other_owner_task_returns_404(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
        other_task = container.task_management.create_task("anon:other", title="them")

        resp = client.get(f"/api/v2/tasks/{other_task.task_id}")
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "TASK_NOT_FOUND"

    def test_nonexistent_returns_404(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        resp = client.get("/api/v2/tasks/task_doesnotexist")
        assert resp.status_code == 404


class TestPatchTask:
    def test_update_title(self, authed_client: tuple[TestClient, dict[str, Any]]) -> None:
        client, user = authed_client
        tid = _seed_task(client, user["user_id"], title="old")
        resp = client.patch(f"/api/v2/tasks/{tid}", json={"title": "new-title"})
        assert resp.status_code == 200
        assert resp.json()["title"] == "new-title"
        # 验证 repo 真的写进去了
        resp2 = client.get(f"/api/v2/tasks/{tid}")
        assert resp2.json()["task"]["title"] == "new-title"

    def test_update_facts_merges(self, authed_client: tuple[TestClient, dict[str, Any]]) -> None:
        client, user = authed_client
        tid = _seed_task(client, user["user_id"])
        client.patch(f"/api/v2/tasks/{tid}", json={"collected_facts": {"a": 1}})
        client.patch(f"/api/v2/tasks/{tid}", json={"collected_facts": {"b": 2}})
        resp = client.get(f"/api/v2/tasks/{tid}")
        assert resp.json()["task"]["collected_facts"] == {"a": 1, "b": 2}

    def test_patch_404_when_not_owner(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
        other_task = container.task_management.create_task("anon:other", title="x")
        resp = client.patch(f"/api/v2/tasks/{other_task.task_id}", json={"title": "hijack"})
        assert resp.status_code == 404


class TestDeleteTask:
    def test_owner_can_delete(self, authed_client: tuple[TestClient, dict[str, Any]]) -> None:
        client, user = authed_client
        tid = _seed_task(client, user["user_id"])
        resp = client.delete(f"/api/v2/tasks/{tid}")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        # 再 GET 应当 404
        assert client.get(f"/api/v2/tasks/{tid}").status_code == 404

    def test_delete_other_owner_returns_404(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
        other_task = container.task_management.create_task("anon:other", title="x")
        resp = client.delete(f"/api/v2/tasks/{other_task.task_id}")
        assert resp.status_code == 404
        # 别人的 task 仍存在
        assert container.task_management.get_task(other_task.task_id, "anon:other") is not None
