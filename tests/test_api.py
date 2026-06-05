"""test_api.py — FastAPI 路由集成测试（使用 TestClient，Mock 外部依赖）。"""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """创建测试用 FastAPI TestClient，Mock KnowledgeService。"""
    # Mock 掉 KnowledgeService 的初始化
    with patch("api.routes._knowledge_service") as mock_ks, \
         patch("api.routes._service_ready") as mock_ready:
        mock_ready.is_set.return_value = True
        mock_ready.wait.return_value = None

        # 设置 mock 返回值
        mock_ks.vector_store = MagicMock()
        mock_ks.vector_store.get_total_count.return_value = 42

        # 需要在 import 之前 mock
        import api.routes as routes_module
        routes_module._knowledge_service = mock_ks
        routes_module._service_ready = mock_ready

        from main import app
        yield TestClient(app), mock_ks


class TestHealthCheck:
    """健康检查端点测试。"""

    def test_health_ok(self, client):
        test_client, mock_ks = client
        resp = test_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("ok", "initializing")


class TestAskEndpoints:
    """问答端点测试。"""

    def test_ask_empty_question(self, client):
        """空问题应返回 422。"""
        test_client, _ = client
        resp = test_client.post("/api/ask", json={"question": ""})
        assert resp.status_code == 422

    def test_ask_valid_question(self, client):
        """有效问题应正常响应。"""
        test_client, mock_ks = client
        from retrieval.generation.qa_chain import QAResult, Citation
        mock_ks.ask.return_value = QAResult(
            answer="这是测试回答",
            citations=[Citation(
                source_type="file", source_name="test.pdf",
                title="测试", source_url=None, text_snippet="片段",
            )],
            has_enough_context=True,
        )
        resp = test_client.post("/api/ask", json={"question": "什么是数据出境？"})
        assert resp.status_code == 200
        data = resp.json()
        assert "answer" in data
        assert len(data["citations"]) == 1


class TestRetrieveEndpoints:
    """纯检索端点测试。"""

    def test_retrieve_valid(self, client):
        """有效检索请求应正常响应。"""
        test_client, mock_ks = client
        from service import RetrievalResult
        mock_ks.retrieve.return_value = RetrievalResult(
            chunks=[{
                "text": "检索到的文本",
                "metadata": {"source_type": "file", "source_name": "test.pdf",
                             "title": "测试", "category": "法规"},
                "distance": 0.3,
            }],
            query_used=["数据出境"],
        )
        resp = test_client.post("/api/retrieve", json={"query": "数据出境"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["chunks"]) == 1
        assert len(data["query_used"]) >= 1


class TestRequestId:
    """请求 ID 中间件测试。"""

    def test_response_has_request_id(self, client):
        """响应应包含 X-Request-ID 头。"""
        test_client, _ = client
        resp = test_client.get("/health")
        assert "X-Request-ID" in resp.headers

    def test_custom_request_id_echoed(self, client):
        """客户端传入的 X-Request-ID 应被回传。"""
        test_client, _ = client
        resp = test_client.get("/health", headers={"X-Request-ID": "test-123"})
        assert resp.headers["X-Request-ID"] == "test-123"
