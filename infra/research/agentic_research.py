"""``ResearchPort`` 适配器：包装 v1 ``retrieval.agent.agentic_rag.AgenticRAGAgent``。

完成两件事：
1. 把 v1 已验证的 Agentic 研究引擎（分类 → 改写 → 多轮检索 → 证据检查 → 联网补齐 →
   ``ReportGenerator`` 生成长报告）统一暴露为单一 ``research()`` 入口。
2. 把 v1 ``AgenticRAGResult`` 翻译成 domain ``ResearchReport``（含 ``Citation`` 与决策步骤）。

懒加载（与 Step 027 同策略）：``AgenticRAGAgent.__init__`` 会 new ``Embedder`` /
``VectorStore`` / ``QuestionClassifier`` 等一串组件，且本适配器注入的 ``build_reranker()``
会同步加载 ~1GB CrossEncoder。绝不能在容器构造 / app import 时构造，否则
``from main import app``（live 测试 + 生产 lifespan）会被阻塞。故首次 ``research()`` 才装配。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, Protocol

from domain.models import Citation, ResearchReport, ResearchStep

if TYPE_CHECKING:
    from collections.abc import Generator

    from retrieval.agent.agentic_rag import AgenticRAGResult


class _ResearchEngineLike(Protocol):
    """v1 ``AgenticRAGAgent`` 的最小契约（research / research_stream）。"""

    def research(
        self,
        query: str,
        mode: str = ...,
        top_k: int = ...,
        enable_web_search: bool = ...,
    ) -> AgenticRAGResult: ...

    def research_stream(
        self,
        query: str,
        mode: str = ...,
        top_k: int = ...,
        enable_web_search: bool = ...,
    ) -> Generator: ...


class AgenticResearchAdapter:
    """实现 ``ResearchPort``，委托给 v1 ``AgenticRAGAgent``。"""

    def __init__(self, agent: _ResearchEngineLike | None = None) -> None:
        # agent=None → 懒构造：首次 research 时才装配 v1 引擎并注入 build_reranker()。
        self._agent = agent

    def _ensure_agent(self) -> _ResearchEngineLike:
        if self._agent is None:
            from retrieval.agent.agentic_rag import AgenticRAGAgent
            from retrieval.search.embedder import Embedder
            from retrieval.search.reranker import build_reranker
            from retrieval.search.vector_store import VectorStore

            self._agent = AgenticRAGAgent(
                embedder=Embedder(),
                vector_store=VectorStore(),
                reranker=build_reranker(),
            )
        return self._agent

    def warmup(self) -> None:
        """预热：提前装配 v1 引擎（含 ~1GB CrossEncoder 加载），把冷启动成本前置到启动期。

        幂等——已装配则直接返回。供 ``main.lifespan`` 在后台线程调用，避免首次
        ``research`` 时让用户干等模型加载（深度研究首步原本要等约 1.5 分钟冷启动）。
        """
        self._ensure_agent()

    def research(
        self,
        query: str,
        *,
        top_k: int = 8,
        enable_web_search: bool = True,
    ) -> ResearchReport:
        result = self._ensure_agent().research(
            query,
            mode="report",
            top_k=top_k,
            enable_web_search=enable_web_search,
        )
        return _to_research_report(result)

    def research_stream(
        self,
        query: str,
        *,
        top_k: int = 8,
        enable_web_search: bool = True,
    ) -> Iterator[ResearchStep | ResearchReport]:
        """流式版 ``research``：逐步 yield 进度 ``ResearchStep``，末尾 yield 完整 ``ResearchReport``。

        v1 引擎 ``research_stream`` 产出 ``{"type": "step"/"token"/"result"/"done"}`` 事件。
        这里把每个 ``step`` 翻译成 domain ``ResearchStep`` 即时 yield（供上层渲染成
        ``thought``，让前端在数分钟的检索/生成期间持续看到进度），``token`` 累积成报告正文，
        ``result`` 携带 citations 与元数据，最终组装成 ``ResearchReport`` 收尾。

        引入动机：``research()`` 同步阻塞可达数分钟，期间前端零反馈（只有 SSE 心跳注释，
        客户端忽略），表现为"深度研究无反应"。流式化后每一步实时推送。
        """
        stream = self._ensure_agent().research_stream(
            query,
            mode="report",
            top_k=top_k,
            enable_web_search=enable_web_search,
        )
        answer_parts: list[str] = []
        steps: list[ResearchStep] = []
        result_data: dict[str, Any] = {}
        for item in stream:
            kind = item.get("type")
            if kind == "step":
                data = item.get("data", {})
                step_name = str(data.get("step") or "step")
                step = ResearchStep(
                    step_name=step_name,
                    description=str(data.get("description") or ""),
                )
                steps.append(step)
                yield step
            elif kind == "token":
                answer_parts.append(str(item.get("content") or ""))
            elif kind == "result":
                result_data = item.get("data", {}) or {}
            # "done" → 终止；不需特殊处理

        citations = [_to_citation(c) for c in result_data.get("citations", [])]
        yield ResearchReport(
            answer="".join(answer_parts),
            citations=citations,
            question_type=str(result_data.get("question_type") or ""),
            question_type_label=str(result_data.get("question_type_label") or ""),
            retrieval_rounds=int(result_data.get("retrieval_rounds") or 0),
            total_docs=int(result_data.get("total_docs") or 0),
            web_search_used=bool(result_data.get("web_search_used", False)),
            refused=bool(result_data.get("refused", False)),
            steps=steps,
        )


def _to_research_report(result: AgenticRAGResult) -> ResearchReport:
    """把 v1 ``AgenticRAGResult`` 翻译成 domain ``ResearchReport``。"""
    return ResearchReport(
        answer=result.answer,
        citations=[_to_citation(c) for c in result.citations],
        question_type=result.question_type,
        question_type_label=result.question_type_label,
        retrieval_rounds=result.retrieval_rounds,
        total_docs=result.total_docs_retrieved,
        web_search_used=result.web_search_used,
        refused=result.refused,
        steps=[
            ResearchStep(
                step_name=s.step_name,
                description=s.description,
                result_summary=s.result_summary,
            )
            for s in result.steps
        ],
    )


def _to_citation(raw: dict[str, Any]) -> Citation:
    """v1 citation dict → domain ``Citation``（source_type/source_name 兜底非空）。"""
    return Citation(
        source_type=str(raw.get("source_type") or "unknown"),
        source_name=str(raw.get("source_name") or "未知来源"),
        title=str(raw.get("title") or ""),
        source_url=raw.get("source_url"),
        text_snippet=str(raw.get("text_snippet") or ""),
    )
