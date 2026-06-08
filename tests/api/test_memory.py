"""``/api/v2/memory/*`` 路由测试：L3 画像查询 + 主动遗忘（Step 030d）。

策略：
- 401 鉴权门：require_owner 缺 cookie → 401
- 注入 ``FakeMemory`` 到 container（运行时读 ``container.memory`` /
  ``container.forget_memory``），断言 owner 隔离 + 删除计数 + 优雅降级
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.container import AppContainer
from app.use_cases.forget_memory import ForgetMemoryUseCase
from app.use_cases.memory_settings import MemorySettingsUseCase
from domain.models import Fact, MemorySettings, SessionProfile
from tests.fakes.fake_memory import FakeMemory
from tests.fakes.fake_memory_settings_store import InMemoryMemorySettingsStore


def _fact(owner: str, text: str) -> Fact:
    return Fact(fact_id=f"f_{abs(hash(text)) % 9999}", owner_id=owner, text=text)


def _inject_memory(client: TestClient, mem: FakeMemory) -> None:
    container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
    container.memory = mem
    container.forget_memory = ForgetMemoryUseCase(mem, audit_log=container.audit_log)


def _inject_settings(
    client: TestClient, store: InMemoryMemorySettingsStore
) -> None:
    container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
    container.memory_settings = MemorySettingsUseCase(
        store, audit_log=container.audit_log
    )


class TestAuthGating:
    def test_profile_unauthed_returns_401(self, client: TestClient) -> None:
        resp = client.get("/api/v2/memory/profile")
        assert resp.status_code == 401

    def test_forget_unauthed_returns_401(self, client: TestClient) -> None:
        resp = client.post("/api/v2/memory/forget", json={"scope": "memory"})
        assert resp.status_code == 401


class TestProfile:
    def test_empty_profile_for_fresh_owner(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        _inject_memory(client, FakeMemory())

        resp = client.get("/api/v2/memory/profile")

        assert resp.status_code == 200
        body = resp.json()
        assert body["facts"] == {}

    def test_returns_owner_profile(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, user = authed_client
        owner = user["user_id"]
        _inject_memory(
            client,
            FakeMemory(
                profiles={owner: SessionProfile(owner_id=owner, facts={"语言": "中文"})}
            ),
        )

        resp = client.get("/api/v2/memory/profile")

        assert resp.status_code == 200
        assert resp.json()["facts"] == {"语言": "中文"}


class TestForget:
    def test_forget_returns_counts(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, user = authed_client
        owner = user["user_id"]
        mem = FakeMemory(
            owners={"t1": owner},
            facts={owner: [_fact(owner, "事实A")]},
            profiles={owner: SessionProfile(owner_id=owner, facts={"k": "v"})},
        )
        _inject_memory(client, mem)

        resp = client.post("/api/v2/memory/forget", json={"scope": "memory"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["scope"] == "memory"
        assert body["facts_deleted"] == 1
        assert body["profile_deleted"] == 1
        assert body["total_deleted"] >= 2
        assert mem.forget_calls == [(owner, "memory")]

    def test_default_scope_is_memory(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, user = authed_client
        mem = FakeMemory()
        _inject_memory(client, mem)

        resp = client.post("/api/v2/memory/forget", json={})

        assert resp.status_code == 200
        assert resp.json()["scope"] == "memory"

    def test_invalid_scope_returns_422(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        _inject_memory(client, FakeMemory())

        resp = client.post("/api/v2/memory/forget", json={"scope": "everything"})

        assert resp.status_code == 422


class TestDegradation:
    def test_profile_when_memory_disabled(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
        container.memory = None
        container.forget_memory = ForgetMemoryUseCase(None, audit_log=container.audit_log)

        resp = client.get("/api/v2/memory/profile")

        assert resp.status_code == 200
        assert resp.json()["facts"] == {}

    def test_forget_when_memory_disabled(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
        container.memory = None
        container.forget_memory = ForgetMemoryUseCase(None, audit_log=container.audit_log)

        resp = client.post("/api/v2/memory/forget", json={"scope": "all"})

        assert resp.status_code == 200
        assert resp.json()["total_deleted"] == 0


class TestSettingsAuthGating:
    def test_get_settings_unauthed_returns_401(self, client: TestClient) -> None:
        assert client.get("/api/v2/memory/settings").status_code == 401

    def test_put_settings_unauthed_returns_401(self, client: TestClient) -> None:
        resp = client.put(
            "/api/v2/memory/settings", json={"use_saved_memory": False}
        )
        assert resp.status_code == 401

    def test_facts_unauthed_returns_401(self, client: TestClient) -> None:
        assert client.get("/api/v2/memory/facts").status_code == 401


class TestSettings:
    def test_default_fresh_owner(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        _inject_settings(client, InMemoryMemorySettingsStore())

        resp = client.get("/api/v2/memory/settings")

        assert resp.status_code == 200
        body = resp.json()
        assert body["use_saved_memory"] is True
        # 对齐 ChatGPT：参考历史聊天记录默认关
        assert body["reference_history"] is False

    def test_put_then_get_roundtrip(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        _inject_settings(client, InMemoryMemorySettingsStore())

        put = client.put(
            "/api/v2/memory/settings", json={"use_saved_memory": False}
        )
        assert put.status_code == 200
        assert put.json()["use_saved_memory"] is False
        assert put.json()["reference_history"] is False  # 未传 → 保持默认（关）

        got = client.get("/api/v2/memory/settings")
        assert got.json()["use_saved_memory"] is False

    def test_owner_isolation(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, user = authed_client
        store = InMemoryMemorySettingsStore()
        store.upsert(
            MemorySettings(
                owner_id="someone_else",
                use_saved_memory=False,
                reference_history=False,
            )
        )
        _inject_settings(client, store)

        # 当前 owner 没有自己的设置 → 默认双开，不读到别人的
        resp = client.get("/api/v2/memory/settings")
        assert resp.json()["use_saved_memory"] is True


class TestFacts:
    def test_lists_owner_facts_with_cap(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, user = authed_client
        owner = user["user_id"]
        _inject_memory(
            client,
            FakeMemory(facts={owner: [_fact(owner, "用户在跨境电商行业")]}),
        )

        resp = client.get("/api/v2/memory/facts")

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["facts"][0]["text"] == "用户在跨境电商行业"
        assert body["cap"] >= 1

    def test_facts_when_memory_disabled(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
        container.memory = None

        resp = client.get("/api/v2/memory/facts")

        assert resp.status_code == 200
        assert resp.json()["count"] == 0


pytestmark = pytest.mark.integration
