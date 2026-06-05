"""test_schemas.py — Pydantic 请求/响应模型验证测试。"""

import pytest
from pydantic import ValidationError
from api.schemas import (
    AskRequest,
    ResearchRequest,
)


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
