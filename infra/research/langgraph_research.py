"""基于 LangGraph 的深度研究工作流。"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from domain.models import Citation, ResearchReport, ResearchStep

if TYPE_CHECKING:
    from domain.ports import ChatPort, RetrievePort, WebSearchPort

_MAX_ROUNDS = 3


class _ResearchState(TypedDict, total=False):
    query: str
    owner_id: str
    top_k: int
    enable_web_search: bool
    queries: list[str]
    documents: list[dict[str, Any]]
    retrieval_round: int
    verdict: str
    supplement_queries: list[str]
    web_search_used: bool
    report: ResearchReport


class LangGraphResearchAdapter:
    """问题规划、迭代检索、证据检查和报告生成的显式状态图。"""

    def __init__(
        self,
        *,
        retriever: RetrievePort,
        web_search: WebSearchPort,
        chat: ChatPort,
    ) -> None:
        self._retriever = retriever
        self._web_search = web_search
        self._chat = chat
        self._graph = self._build_graph()

    def warmup(self) -> None:
        """图在构造期已编译；保留方法供应用启动钩子统一调用。"""

    def research(
        self,
        query: str,
        *,
        owner_id: str | None = None,
        top_k: int = 8,
        enable_web_search: bool = True,
    ) -> ResearchReport:
        report: ResearchReport | None = None
        for item in self.research_stream(
            query,
            owner_id=owner_id,
            top_k=top_k,
            enable_web_search=enable_web_search,
        ):
            if isinstance(item, ResearchReport):
                report = item
        if report is None:
            raise RuntimeError("LangGraph Deep Research 未生成报告")
        return report

    def research_stream(
        self,
        query: str,
        *,
        owner_id: str | None = None,
        top_k: int = 8,
        enable_web_search: bool = True,
    ) -> Iterator[ResearchStep | ResearchReport]:
        state: _ResearchState = {
            "query": query,
            "owner_id": owner_id or "",
            "top_k": top_k,
            "enable_web_search": enable_web_search,
            "queries": [query],
            "documents": [],
            "retrieval_round": 0,
            "web_search_used": False,
        }
        final_report: ResearchReport | None = None
        for update in self._graph.stream(state, stream_mode="updates"):
            node_name, payload = next(iter(update.items()))
            if node_name == "plan":
                queries = payload.get("queries", [])
                yield ResearchStep(
                    step_name="plan",
                    description="LangGraph 规划研究查询",
                    result_summary=f"生成 {len(queries)} 个查询",
                )
            elif node_name == "retrieve":
                documents = payload.get("documents", [])
                yield ResearchStep(
                    step_name="retrieve",
                    description=f"第 {payload.get('retrieval_round', 0)} 轮混合检索",
                    result_summary=f"累计 {len(documents)} 条证据",
                )
            elif node_name == "assess":
                yield ResearchStep(
                    step_name="assess",
                    description="评估证据充分性",
                    result_summary=str(payload.get("verdict") or "unknown"),
                )
            elif node_name == "web_search":
                yield ResearchStep(
                    step_name="web_search",
                    description="知识库证据不足，搜索公开监管资料",
                    result_summary=f"累计 {len(payload.get('documents', []))} 条证据",
                )
            elif node_name == "generate":
                final_report = payload.get("report")
                yield ResearchStep(
                    step_name="generate",
                    description="生成带来源标记的研究报告",
                    result_summary=(
                        f"{len(final_report.answer)} 字"
                        if isinstance(final_report, ResearchReport)
                        else "生成失败"
                    ),
                )
        if final_report is None:
            raise RuntimeError("LangGraph Deep Research 未生成报告")
        yield final_report

    def _build_graph(self) -> Any:
        graph = StateGraph(_ResearchState)
        graph.add_node("plan", self._plan)
        graph.add_node("retrieve", self._retrieve)
        graph.add_node("assess", self._assess)
        graph.add_node("web_search", self._search_web)
        graph.add_node("generate", self._generate)
        graph.add_edge(START, "plan")
        graph.add_edge("plan", "retrieve")
        graph.add_edge("retrieve", "assess")
        graph.add_conditional_edges(
            "assess",
            self._route_after_assessment,
            {
                "retrieve": "retrieve",
                "web_search": "web_search",
                "generate": "generate",
            },
        )
        graph.add_edge("web_search", "generate")
        graph.add_edge("generate", END)
        return graph.compile(name="riskpilot_deep_research")

    def _plan(self, state: _ResearchState) -> dict[str, Any]:
        query = state["query"]
        prompt = f"""把下面的数据出境合规研究问题拆成最多 3 个检索查询。
只输出 JSON：{{"queries": ["..."], "question_type": "definition|comparison|condition|process|case"}}

问题：{query}"""
        data = _safe_json(
            self._chat.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.0,
                json_mode=True,
            )
        )
        queries = data.get("queries") if isinstance(data, dict) else None
        normalized = [
            str(item).strip()
            for item in (queries if isinstance(queries, list) else [])
            if str(item).strip()
        ]
        return {"queries": list(dict.fromkeys([query, *normalized]))[:4]}

    def _retrieve(self, state: _ResearchState) -> dict[str, Any]:
        documents = list(state.get("documents", []))
        seen = {str(document.get("chunk_id")) for document in documents}
        owner_id = state.get("owner_id") or None
        top_k = state.get("top_k", 8)
        for query in state.get("queries", [state["query"]]):
            for chunk in self._retriever.retrieve(
                query,
                top_k=top_k,
                corpus="law",
                owner_id=owner_id,
            ):
                if chunk.chunk_id in seen:
                    continue
                seen.add(chunk.chunk_id)
                documents.append(
                    {
                        "chunk_id": chunk.chunk_id,
                        "text": chunk.text,
                        "source_type": chunk.source_type,
                        "source_name": chunk.source_name,
                        "title": chunk.title,
                        "source_url": chunk.source_url,
                        "score": chunk.score,
                    }
                )
        return {
            "documents": documents,
            "retrieval_round": state.get("retrieval_round", 0) + 1,
        }

    def _assess(self, state: _ResearchState) -> dict[str, Any]:
        documents = state.get("documents", [])
        if not documents:
            return {"verdict": "insufficient", "supplement_queries": []}
        excerpts = "\n".join(
            f"[{index}] {document['text'][:500]}"
            for index, document in enumerate(documents[:8], start=1)
        )
        prompt = f"""判断证据是否足以回答研究问题。
只输出 JSON：{{"verdict": "sufficient|partial|insufficient", "supplement_queries": ["..."]}}
补充查询最多 2 个，不能重复原问题。

问题：{state['query']}
证据：
{excerpts}"""
        data = _safe_json(
            self._chat.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.0,
                json_mode=True,
            )
        )
        verdict = str(data.get("verdict") or "sufficient")
        if verdict not in {"sufficient", "partial", "insufficient"}:
            verdict = "sufficient"
        raw_supplements = data.get("supplement_queries")
        supplements = [
            str(item).strip()
            for item in (
                raw_supplements if isinstance(raw_supplements, list) else []
            )
            if str(item).strip()
        ][:2]
        return {
            "verdict": verdict,
            "supplement_queries": supplements,
            "queries": supplements or state.get("queries", [state["query"]]),
        }

    @staticmethod
    def _route_after_assessment(state: _ResearchState) -> str:
        verdict = state.get("verdict", "sufficient")
        round_number = state.get("retrieval_round", 0)
        if (
            verdict == "partial"
            and round_number < _MAX_ROUNDS
            and state.get("supplement_queries")
        ):
            return "retrieve"
        if (
            verdict != "sufficient"
            and state.get("enable_web_search", True)
            and not state.get("web_search_used", False)
        ):
            return "web_search"
        return "generate"

    def _search_web(self, state: _ResearchState) -> dict[str, Any]:
        documents = list(state.get("documents", []))
        seen_urls = {
            str(document.get("source_url"))
            for document in documents
            if document.get("source_url")
        }
        for result in self._web_search.search(state["query"], max_results=3):
            if result.url in seen_urls:
                continue
            seen_urls.add(result.url)
            documents.append(
                {
                    "chunk_id": f"web:{result.url}",
                    "text": result.snippet,
                    "source_type": "web",
                    "source_name": result.title or result.url,
                    "title": result.title,
                    "source_url": result.url,
                    "score": 0.0,
                }
            )
        return {"documents": documents, "web_search_used": True}

    def _generate(self, state: _ResearchState) -> dict[str, Any]:
        documents = state.get("documents", [])
        steps = [
            ResearchStep(
                step_name="plan",
                description="LangGraph 规划研究查询",
                result_summary=f"{len(state.get('queries', []))} 个查询",
            ),
            ResearchStep(
                step_name="retrieve",
                description="执行多轮混合检索",
                result_summary=f"{state.get('retrieval_round', 0)} 轮",
            ),
            ResearchStep(
                step_name="assess",
                description="检查证据充分性",
                result_summary=state.get("verdict", "unknown"),
            ),
        ]
        if state.get("web_search_used"):
            steps.append(
                ResearchStep(
                    step_name="web_search",
                    description="公开网页补充检索",
                    result_summary="已使用",
                )
            )
        citations = [_to_citation(document) for document in documents[:12]]
        if not documents:
            report = ResearchReport(
                answer="现有知识库和公开来源没有足够证据，无法生成可靠研究结论。",
                refused=True,
                retrieval_rounds=state.get("retrieval_round", 0),
                steps=steps,
            )
            return {"report": report}
        evidence = "\n\n".join(
            f"[{document['source_name']}]\n{document['text'][:1200]}"
            for document in documents[:12]
        )
        prompt = f"""基于以下证据撰写数据出境合规研究报告。
要求：结构化 Markdown；关键结论后使用 [来源名]；区分事实、规则和建议；
证据不足时明确保留意见；不得添加证据中不存在的法规条文。

研究问题：{state['query']}

证据：
{evidence}"""
        answer = self._chat.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.1,
        ).strip()
        report = ResearchReport(
            answer=answer,
            citations=citations,
            question_type="research",
            question_type_label="LangGraph 深度研究",
            retrieval_rounds=state.get("retrieval_round", 0),
            total_docs=len(documents),
            web_search_used=state.get("web_search_used", False),
            refused=not bool(answer),
            steps=steps,
        )
        return {"report": report}


def _safe_json(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if match is None:
            return {}
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}


def _to_citation(document: dict[str, Any]) -> Citation:
    return Citation(
        source_type=str(document.get("source_type") or "unknown"),
        source_name=str(document.get("source_name") or "未知来源"),
        title=str(document.get("title") or ""),
        source_url=(
            str(document["source_url"]) if document.get("source_url") else None
        ),
        text_snippet=str(document.get("text") or "")[:500],
    )
