"""``AgenticResearchAdapter`` 测试：result→report 映射 + 懒加载。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from domain.models import ResearchReport
from infra.research.agentic_research import AgenticResearchAdapter


@dataclass
class _AgentStep:
    step_name: str
    description: str
    result_summary: str


@dataclass
class _FakeResult:
    answer: str = ""
    citations: list[dict[str, Any]] = field(default_factory=list)
    question_type: str = ""
    question_type_label: str = ""
    retrieval_rounds: int = 0
    total_docs_retrieved: int = 0
    web_search_used: bool = False
    refused: bool = False
    steps: list[_AgentStep] = field(default_factory=list)


class _FakeEngine:
    """v1 ``AgenticRAGAgent`` 替身：记录调用并返回预设 result。"""

    def __init__(self, result: _FakeResult) -> None:
        self._result = result
        self.calls: list[dict] = []

    def research(
        self,
        query: str,
        mode: str = "report",
        top_k: int = 8,
        enable_web_search: bool = True,
    ) -> _FakeResult:
        self.calls.append(
            {
                "query": query,
                "mode": mode,
                "top_k": top_k,
                "enable_web_search": enable_web_search,
            }
        )
        return self._result


class TestResultMapping:
    def test_maps_all_fields_to_report(self) -> None:
        result = _FakeResult(
            answer="## 报告\n正文",
            citations=[
                {
                    "source_type": "law",
                    "source_name": "个人信息保护法",
                    "title": "第三十八条",
                    "source_url": None,
                    "text_snippet": "向境外提供……",
                }
            ],
            question_type="condition",
            question_type_label="条件类",
            retrieval_rounds=2,
            total_docs_retrieved=7,
            web_search_used=True,
            refused=False,
            steps=[_AgentStep("classify", "识别类型", "condition")],
        )
        adapter = AgenticResearchAdapter(agent=_FakeEngine(result))
        report = adapter.research("个人信息出境条件")

        assert isinstance(report, ResearchReport)
        assert report.answer == "## 报告\n正文"
        assert report.question_type == "condition"
        assert report.question_type_label == "条件类"
        assert report.retrieval_rounds == 2
        assert report.total_docs == 7
        assert report.web_search_used is True
        assert report.refused is False
        assert len(report.steps) == 1
        assert report.steps[0].step_name == "classify"
        assert len(report.citations) == 1
        assert report.citations[0].source_name == "个人信息保护法"

    def test_forwards_report_mode_and_params(self) -> None:
        engine = _FakeEngine(_FakeResult(answer="x"))
        adapter = AgenticResearchAdapter(agent=engine)
        adapter.research("q", top_k=12, enable_web_search=False)
        assert engine.calls == [
            {"query": "q", "mode": "report", "top_k": 12, "enable_web_search": False}
        ]

    def test_citation_defaults_when_fields_missing(self) -> None:
        """v1 citation dict 缺 source_type/source_name 时兜底非空（满足 Citation 约束）。"""
        result = _FakeResult(
            answer="a",
            citations=[{"title": "无来源标识"}],
        )
        adapter = AgenticResearchAdapter(agent=_FakeEngine(result))
        report = adapter.research("q")
        assert report.citations[0].source_type == "unknown"
        assert report.citations[0].source_name == "未知来源"


class TestLazyConstruction:
    def test_injected_agent_used_without_lazy_build(self) -> None:
        engine = _FakeEngine(_FakeResult(answer="ok"))
        adapter = AgenticResearchAdapter(agent=engine)
        # 注入了 agent → _agent 直接是 stub，不触发懒构造
        assert adapter._agent is engine
        adapter.research("q")
        assert engine.calls[0]["query"] == "q"

    def test_default_agent_not_built_on_construction(self) -> None:
        # 不注入 agent → 构造时不装配 v1 引擎（不加载 CrossEncoder）
        adapter = AgenticResearchAdapter()
        assert adapter._agent is None
