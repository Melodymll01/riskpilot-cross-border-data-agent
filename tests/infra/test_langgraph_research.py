"""LangGraph Deep Research 的路由、owner 隔离和失败降级测试。"""

from __future__ import annotations

from domain.models import Chunk, ResearchReport, ResearchStep, WebResult
from infra.research import LangGraphResearchAdapter
from tests.fakes import FakeChat, FakeRetrieve, FakeWebSearch


def _chunk(chunk_id: str = "chunk_001") -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        text="个人信息保护法第三十八条规定了个人信息出境条件。",
        source_type="law",
        source_name="个人信息保护法",
        title="第三十八条",
        score=0.9,
    )


def test_sufficient_evidence_routes_directly_to_generation() -> None:
    retriever = FakeRetrieve([_chunk()])
    chat = FakeChat(
        responses=[
            '{"queries":["个人信息出境 条件"]}',
            '{"verdict":"sufficient","supplement_queries":[]}',
            "## 研究报告\n依据 [个人信息保护法]",
        ]
    )
    research = LangGraphResearchAdapter(
        retriever=retriever,
        web_search=FakeWebSearch(),
        chat=chat,
    )

    items = list(research.research_stream("个人信息如何出境？", owner_id="github:alice"))

    assert isinstance(items[-1], ResearchReport)
    report = items[-1]
    assert report.retrieval_rounds == 1
    assert report.web_search_used is False
    assert report.citations[0].source_name == "个人信息保护法"
    assert all(isinstance(item, (ResearchStep, ResearchReport)) for item in items)
    assert {call["owner_id"] for call in retriever.calls} == {"github:alice"}


def test_partial_evidence_loops_with_supplement_query() -> None:
    retriever = FakeRetrieve([_chunk()])
    chat = FakeChat(
        responses=[
            '{"queries":["安全评估 条件"]}',
            '{"verdict":"partial","supplement_queries":["标准合同 适用条件"]}',
            '{"verdict":"sufficient","supplement_queries":[]}',
            "报告正文",
        ]
    )
    research = LangGraphResearchAdapter(
        retriever=retriever,
        web_search=FakeWebSearch(),
        chat=chat,
    )

    report = research.research("比较三种出境路径", owner_id="anon:alice")

    assert report.retrieval_rounds == 2
    assert any(
        call["query"] == "标准合同 适用条件" for call in retriever.calls
    )


def test_insufficient_evidence_uses_web_search_once() -> None:
    chat = FakeChat(
        responses=[
            '{"queries":[]}',
            '{"verdict":"insufficient","supplement_queries":[]}',
            "基于 [监管通知] 的报告",
        ]
    )
    web = FakeWebSearch(
        [
            WebResult(
                title="监管通知",
                url="https://example.com/notice",
                snippet="最新监管说明",
            )
        ]
    )
    research = LangGraphResearchAdapter(
        retriever=FakeRetrieve(),
        web_search=web,
        chat=chat,
    )

    report = research.research("最新监管变化", owner_id="anon:alice")

    assert report.web_search_used is True
    assert report.citations[0].source_type == "web"
    assert web.calls == [("最新监管变化", 3)]


def test_no_evidence_and_web_disabled_returns_refusal() -> None:
    chat = FakeChat(
        responses=[
            '{"queries":[]}',
            '{"verdict":"insufficient","supplement_queries":[]}',
        ]
    )
    research = LangGraphResearchAdapter(
        retriever=FakeRetrieve(),
        web_search=FakeWebSearch(),
        chat=chat,
    )

    report = research.research(
        "无资料问题",
        owner_id="anon:alice",
        enable_web_search=False,
    )

    assert report.refused is True
    assert report.total_docs == 0
