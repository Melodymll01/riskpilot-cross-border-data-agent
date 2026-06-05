"""Agent 可调用工具的声明式注册表。

每个 ``ToolSpec`` = 名称 + 描述 + 参数 schema + handler 函数。``ask_user`` /
``final_answer`` 是动作类型而非工具，不在此注册。

新增工具 = 新加一条 ``ToolSpec``，不动 Agent 主循环代码。这就是"工程化"的入口。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.container import AppContainer


@dataclass(frozen=True)
class ToolSpec:
    """单个工具的声明。"""

    name: str
    description: str
    parameters_schema: dict[str, Any]
    handler: Callable[..., Any]
    timeout_s: float = 30.0
    requires_owner: bool = True


def register_default_tools(container: AppContainer) -> dict[str, ToolSpec]:
    """构造 Step 009 范围内的工具集：4 个检索/搜索类工具。

    后续步骤会追加：
    - ``risk_profile`` (Step 012 PR-7，依赖 RiskProfilerUseCase)
    - ``generate_checklist`` (同上)
    """

    def _search_law(*, query: str, top_k: int = 5, owner_id: str) -> list[dict[str, Any]]:
        chunks = container.retriever.retrieve(
            query, top_k=top_k, corpus="law", owner_id=owner_id
        )
        return [_chunk_to_dict(c) for c in chunks]

    def _search_user_docs(
        *, query: str, top_k: int = 5, owner_id: str
    ) -> list[dict[str, Any]]:
        chunks = container.retriever.retrieve(
            query, top_k=top_k, corpus="user_docs", owner_id=owner_id
        )
        return [_chunk_to_dict(c) for c in chunks]

    def _web_search(*, query: str, max_results: int = 3, owner_id: str) -> list[dict[str, Any]]:
        results = container.web_search.search(query, max_results=max_results)
        return [
            {"title": r.title, "url": r.url, "snippet": r.snippet} for r in results
        ]

    def _evidence_judge(
        *, factor_id: str, document: str, target: str, owner_id: str
    ) -> dict[str, Any]:
        judgement = container.evidence.judge(
            factor_id, {"document": document, "target": target}
        )
        return {
            "factor_id": judgement.factor_id,
            "label": judgement.label,
            "rationale": judgement.rationale,
            "confidence": judgement.confidence,
        }

    return {
        "search_law": ToolSpec(
            name="search_law",
            description="在法规知识库中检索条款，返回相关条文片段（top_k 默认 5）",
            parameters_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索关键词或自然语言问题"},
                    "top_k": {"type": "integer", "default": 5, "minimum": 1, "maximum": 20},
                },
                "required": ["query"],
            },
            handler=_search_law,
        ),
        "search_user_docs": ToolSpec(
            name="search_user_docs",
            description="在当前用户上传的文档中检索内容；自动按 owner_id 隔离",
            parameters_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "default": 5, "minimum": 1, "maximum": 20},
                },
                "required": ["query"],
            },
            handler=_search_user_docs,
        ),
        "web_search": ToolSpec(
            name="web_search",
            description="联网搜索最新监管口径或公开判例（DuckDuckGo），用于知识库未覆盖的新信息",
            parameters_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "default": 3, "minimum": 1, "maximum": 10},
                },
                "required": ["query"],
            },
            handler=_web_search,
        ),
        "evidence_judge": ToolSpec(
            name="evidence_judge",
            description="对文档片段做 schema-guided 证据判定（单 factor），返回 label+confidence",
            parameters_schema={
                "type": "object",
                "properties": {
                    "factor_id": {"type": "string"},
                    "document": {"type": "string"},
                    "target": {"type": "string"},
                },
                "required": ["factor_id", "document", "target"],
            },
            handler=_evidence_judge,
        ),
    }


def _chunk_to_dict(chunk: Any) -> dict[str, Any]:
    """把 domain.Chunk 序列化成 JSON 友好的 dict 给 LLM 看。"""
    return {
        "chunk_id": chunk.chunk_id,
        "text": chunk.text,
        "source_name": chunk.source_name,
        "title": chunk.title,
        "source_url": chunk.source_url,
        "score": chunk.score,
    }
