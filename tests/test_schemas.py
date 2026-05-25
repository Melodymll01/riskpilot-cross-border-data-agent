"""test_schemas.py — Pydantic 请求/响应模型验证测试。"""

import pytest
from pydantic import ValidationError
from api.schemas import (
    WebIngestRequest,
    AskRequest,
    RetrieveRequest,
    ResearchRequest,
    IngestResponse,
    DeleteSourceResponse,
)


class TestWebIngestRequest:
    """网页采集请求验证。"""

    def test_valid_url(self):
        req = WebIngestRequest(url="https://example.com/article")
        assert req.url == "https://example.com/article"

    def test_http_url(self):
        req = WebIngestRequest(url="http://example.com")
        assert req.url.startswith("http://")

    def test_invalid_scheme(self):
        with pytest.raises(ValidationError, match="仅支持 http/https"):
            WebIngestRequest(url="ftp://example.com/file")

    def test_missing_domain(self):
        with pytest.raises(ValidationError, match="URL 缺少域名"):
            WebIngestRequest(url="https://")

    def test_default_category(self):
        req = WebIngestRequest(url="https://example.com")
        assert req.category == ""


class TestAskRequest:
    """问答请求验证。"""

    def test_valid_request(self):
        req = AskRequest(question="什么是数据出境？")
        assert req.question == "什么是数据出境？"
        assert req.top_k == 5  # 默认值

    def test_empty_question_rejected(self):
        with pytest.raises(ValidationError):
            AskRequest(question="")

    def test_top_k_bounds(self):
        req = AskRequest(question="测试", top_k=1)
        assert req.top_k == 1

        with pytest.raises(ValidationError):
            AskRequest(question="测试", top_k=0)

        with pytest.raises(ValidationError):
            AskRequest(question="测试", top_k=21)

    def test_question_max_length(self):
        with pytest.raises(ValidationError):
            AskRequest(question="x" * 2001)


class TestResearchRequest:
    """Agentic RAG 深度研究请求验证。"""

    def test_valid_report_mode(self):
        req = ResearchRequest(query="数据出境安全评估")
        assert req.mode == "report"

    def test_qa_mode(self):
        req = ResearchRequest(query="测试", mode="qa")
        assert req.mode == "qa"

    def test_invalid_mode_rejected(self):
        with pytest.raises(ValidationError, match="report.*qa"):
            ResearchRequest(query="测试", mode="invalid")


class TestDeleteSourceResponse:
    """删除来源响应模型。"""

    def test_with_all_fields(self):
        resp = DeleteSourceResponse(success=True, message="删除成功", deleted_count=5)
        assert resp.success is True
        assert resp.deleted_count == 5

    def test_defaults(self):
        resp = DeleteSourceResponse(success=False)
        assert resp.message == ""
        assert resp.deleted_count == 0
