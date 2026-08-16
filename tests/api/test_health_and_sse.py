"""``/api/v2/health`` + ``/api/v2/health/ready`` 测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health(client: TestClient) -> None:
    resp = client.get("/api/v2/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "version": "v2"}


def test_ready_lists_tools(client: TestClient) -> None:
    resp = client.get("/api/v2/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    # 全部 8 个 Port 装配完毕
    assert all(body["ports_loaded"].values())
    # Step 009 注册的 4 个工具
    assert set(body["tools"]) == {
        "search_law",
        "search_user_docs",
        "web_search",
        "evidence_judge",
    }


def test_sse_serialization() -> None:
    """单测 SSE 工具函数（不经过 HTTP 层）。"""
    from api.v2.sse import event_to_sse, sse_error, sse_keepalive
    from domain.agent import AgentEvent

    frame = event_to_sse(AgentEvent.thought("先查\n法条"))
    # 帧体不应包含裸换行（除了 event/data 行之间和帧尾的 \n\n）
    body_line = next(ln for ln in frame.splitlines() if ln.startswith("data:"))
    assert "\n" not in body_line  # 单行 data 不被裸换行截断
    assert frame.startswith("event: thought\n")
    assert frame.endswith("\n\n")
    # JSON 把真正的换行编码成 \n 字面量；这是 JSON 标准，不影响 SSE 解析
    assert r"先查\n法条" in frame

    keep = sse_keepalive()
    assert keep == ": keepalive\n\n"

    err = sse_error("BOOM", "天塌了")
    assert err.startswith("event: error\n")
    assert "BOOM" in err
