"""集成测试：``main:app`` 挂载当前 ``/api/v2/*`` 对话接口。

策略：
- 不再用 fresh FastAPI + 全 Fake container（那是 unit-level 测试，见 tests/api/）
- 这里走真 main.app，验证装配确实生效；TestClient 启动会执行运行配置门禁，
  因此在 import main 之前显式切换为无需外部密钥的 local 通道。
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def main_client():
    """使用 main.app 的 TestClient（模块级缓存，避免重复 init）。"""
    from fastapi.testclient import TestClient

    import main as main_module

    # 避免集成测试依赖后台预热时序。

    with TestClient(main_module.app) as c:
        yield c


def test_main_app_has_container_in_state(main_client):
    """容器装在 app.state 上，方便后续中间件 / 自检脚本读取。"""
    import main as main_module

    assert hasattr(main_module.app.state, "container")
    assert main_module.app.state.container.copilot_agent.tool_names == [
        "assess_risk",
        "request_clarification",
        "search_law",
        "search_user_docs",
        "web_search",
    ]


def test_v2_health_works(main_client):
    resp = main_client.get("/api/v2/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "version": "v2"}


def test_v2_liveness_works(main_client):
    resp = main_client.get("/api/v2/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_v2_ready_lists_all_tools(main_client):
    resp = main_client.get("/api/v2/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"] is True
    assert body["checks"]["ready"] is True
    assert set(body["tools"]) == {
        "assess_risk",
        "request_clarification",
        "search_law",
        "search_user_docs",
        "web_search",
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


def test_openapi_contains_conversation_routes(main_client):
    paths = main_client.get("/openapi.json").json()["paths"]
    assert "/api/v2/copilot/chat/stream" in paths
    assert "/api/v2/copilot/runs/{run_id}/cancel" in paths
    assert "/api/v2/files" in paths
    assert "/api/v2/qa" in paths
    assert not any(path.startswith("/api/v3/") for path in paths)


def test_legacy_root_still_served(main_client):
    """老的 ``GET /`` 静态前端入口保留（由 main.py 直接服务，非 v1 路由）。"""
    resp = main_client.get("/")
    assert resp.status_code == 200
    assert 'id="app"' in resp.text
    assert 'id="composer-input"' in resp.text
    assert 'id="btn-stop"' in resp.text
