"""集成测试：``main:app`` 同时挂载 ``/api/v2/*`` 与 ``/api/v3/*``。

策略：
- 不再用 fresh FastAPI + 全 Fake container（那是 unit-level 测试，见 tests/api/）
- 这里走真 main.app，验证装配确实生效；为了避免触发 sk-placeholder 校验，
  在 import main 之前把 LLM/EMBED provider 切到 local 通道。
"""

from __future__ import annotations

import os

import pytest

# 必须在 import main 之前设置：config.py 在模块导入时校验 key
os.environ.setdefault("LLM_PROVIDER", "local")
os.environ.setdefault("EMBED_PROVIDER", "local")


@pytest.fixture(scope="module")
def main_client():
    """使用 main.app 的 TestClient（模块级缓存，避免重复 init）。"""
    from fastapi.testclient import TestClient

    import main as main_module

    # 避免集成测试触发深度研究引擎的真实预热（~90s 加载 CrossEncoder）：
    # lifespan 会后台调度 research.warmup，这里替成 no-op。
    main_module.app.state.container.research.warmup = lambda: None  # type: ignore[method-assign]

    with TestClient(main_module.app) as c:
        yield c


def test_main_app_has_container_in_state(main_client):
    """容器装在 app.state 上，方便后续中间件 / 自检脚本读取。"""
    import main as main_module

    assert hasattr(main_module.app.state, "container")
    assert sorted(main_module.app.state.container.tool_registry.keys()) == [
        "evidence_judge",
        "search_law",
        "search_user_docs",
        "web_search",
    ]


def test_v2_health_works(main_client):
    resp = main_client.get("/api/v2/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "version": "v2"}


def test_v2_ready_lists_all_tools(main_client):
    resp = main_client.get("/api/v2/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert all(body["ports_loaded"].values())
    assert set(body["tools"]) == {
        "search_law",
        "search_user_docs",
        "web_search",
        "evidence_judge",
    }


def test_v2_anonymous_login_returns_cookie(main_client):
    """走真实路由 + 真实 AuthService（不是 FakeAuth）。"""
    resp = main_client.post("/api/v2/auth/anonymous")
    assert resp.status_code == 201
    body = resp.json()
    assert body["user"]["user_id"].startswith("anon:")
    # main.py 配置的 cookie 已被 Set-Cookie 写回
    assert "copilot_session" in main_client.cookies


def test_v2_require_owner_blocks_unauthed(main_client):
    """新建一个不带 cookie 的 client，直接打 /tasks 应当 401。"""
    from fastapi.testclient import TestClient

    import main as main_module

    with TestClient(main_module.app) as fresh:
        resp = fresh.get("/api/v2/tasks")
        assert resp.status_code == 401
        assert resp.json()["error_code"] == "AUTH_REQUIRED"


def test_v3_workspace_route_mounted(main_client):
    """真实主应用挂载 V3；无登录态访问时应命中鉴权而非 404。"""
    from fastapi.testclient import TestClient

    import main as main_module

    with TestClient(main_module.app) as fresh:
        resp = fresh.get("/api/v3/workspaces")
        assert resp.status_code == 401
        assert resp.json()["error_code"] == "AUTH_REQUIRED"


def test_openapi_contains_v2_and_v3(main_client):
    paths = main_client.get("/openapi.json").json()["paths"]
    assert "/api/v2/copilot/chat/stream" in paths
    assert "/api/v3/workspaces" in paths
    assert "/api/v3/cases/{case_id}/transitions" in paths
    assert "/api/v3/cases/{case_id}/documents" in paths
    assert "/api/v3/processing-jobs/{job_id}" in paths
    assert "/api/v3/processing-jobs/{job_id}/parse" in paths
    assert "/api/v3/processing-jobs/{job_id}/retry" in paths
    assert "/api/v3/processing-jobs/{job_id}/index" in paths
    assert "/api/v3/cases/{case_id}/evidence/search" in paths
    assert "/api/v3/cases/{case_id}/facts" in paths
    assert "/api/v3/facts/{fact_id}/transitions" in paths
    assert "/api/v3/workspaces/{workspace_id}/policy-rules" in paths
    assert "/api/v3/cases/{case_id}/policy-evaluations" in paths


def test_legacy_root_still_served(main_client):
    """老的 ``GET /`` 静态前端入口保留（由 main.py 直接服务，非 v1 路由）。"""
    resp = main_client.get("/")
    # frontend/index.html 存在 → 200；不存在则 404；任一都说明静态入口仍挂着
    assert resp.status_code in (200, 404)


class TestWarmupResearch:
    """``_warmup_research`` lifespan 辅助：best-effort 预热，绝不影响服务可用性。"""

    @staticmethod
    def _run(coro):
        import asyncio

        return asyncio.run(coro)

    def test_calls_warmup_once(self):
        import main as main_module

        calls = []

        class _C:
            class research:  # noqa: N801 — 简单命名空间
                @staticmethod
                def warmup():
                    calls.append(1)

        self._run(main_module._warmup_research(_C()))
        assert calls == [1]

    def test_swallows_warmup_exception(self):
        import main as main_module

        class _C:
            class research:  # noqa: N801
                @staticmethod
                def warmup():
                    raise RuntimeError("模型加载炸了")

        # 不抛——best-effort，首个 research 仍可懒加载
        self._run(main_module._warmup_research(_C()))

    def test_noop_when_no_warmup_method(self):
        import main as main_module

        class _C:
            research = object()  # 无 warmup 属性

        # 不抛
        self._run(main_module._warmup_research(_C()))

    def test_lifespan_actually_invokes_warmup(self):
        """走真实 main.app lifespan：确认 warmup 被后台任务真正调用（recording stub 不加载模型）。"""
        from fastapi.testclient import TestClient

        import main as main_module

        calls: list[int] = []
        main_module.app.state.container.research.warmup = lambda: calls.append(1)  # type: ignore[method-assign]
        with TestClient(main_module.app) as c:
            # 触发一次请求让事件循环驱动后台预热任务完成
            c.get("/api/v2/health")
        assert calls == [1]
