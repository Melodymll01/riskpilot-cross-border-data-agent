"""`ResearchPort` Fake：返回预设 ResearchReport，记录调用。"""

from __future__ import annotations

from domain.models import ResearchReport, ResearchStep


class FakeResearch:
    def __init__(self, report: ResearchReport | None = None) -> None:
        self._report = report or ResearchReport(
            answer="## 深度研究报告\n\n这是一份示例报告。",
            question_type="definition",
            question_type_label="定义类",
            retrieval_rounds=2,
            total_docs=6,
            web_search_used=False,
            refused=False,
            steps=[
                ResearchStep(
                    step_name="classify",
                    description="识别问题类型: 定义类",
                    result_summary="类型=definition",
                ),
                ResearchStep(
                    step_name="generate",
                    description="基于 6 条文档生成深度报告",
                    result_summary="800 字, 3 个来源",
                ),
            ],
        )
        self.calls: list[dict] = []

    def research_stream(
        self,
        query: str,
        *,
        owner_id: str | None = None,
        top_k: int = 8,
        enable_web_search: bool = True,
    ):
        self.calls.append(
            {
                "query": query,
                "owner_id": owner_id,
                "top_k": top_k,
                "enable_web_search": enable_web_search,
            }
        )
        yield from self._report.steps
        yield self._report

    def research(
        self,
        query: str,
        *,
        owner_id: str | None = None,
        top_k: int = 8,
        enable_web_search: bool = True,
    ) -> ResearchReport:
        self.calls.append(
            {
                "query": query,
                "owner_id": owner_id,
                "top_k": top_k,
                "enable_web_search": enable_web_search,
            }
        )
        return self._report
