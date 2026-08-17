"""结构化 EvidencePlan Planner。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.messages.ai import AIMessage
from pydantic import ValidationError

from domain.agent_workflow import EvidencePlan, EvidencePlanRequest, EvidencePlanResult

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

_SYSTEM = """你是 RiskPilot 案件证据规划器。

只规划调查问题、必需事实字段、允许工具、证据缺口和完成标准。
不得生成合规结论，不得批准 Assessment，不得修改权限或 scope。
available_tools 之外的工具不得输出。只返回 JSON，不输出思维链。

JSON:
{
  "investigation_questions": ["..."],
  "required_fact_fields": ["..."],
  "planned_tools": ["..."],
  "evidence_gaps": ["..."],
  "completion_criteria": ["..."]
}
"""

_MANDATORY_TOOLS = {
    "retrieve_regulations",
    "evaluate_deterministic_rules",
    "verify_claim_citations",
}


class DeterministicEvidencePlanner:
    """离线协议基线：由 required fields 和固定工具顺序构造可解释计划。"""

    def build_plan(self, request: EvidencePlanRequest) -> EvidencePlanResult:
        tools = [
            name
            for name in (
                "retrieve_case_evidence",
                "retrieve_regulations",
                "extract_fact_candidates",
                "evaluate_deterministic_rules",
                "verify_claim_citations",
            )
            if name in request.available_tools
        ]
        gaps = (
            []
            if request.ready_document_count > 0
            else ["当前没有 ready 案件材料，无法建立原文证据链"]
        )
        return EvidencePlanResult(
            plan=EvidencePlan(
                investigation_questions=(
                    [
                        f"字段 {field_name} 的当前证据和确认状态是什么？"
                        for field_name in request.required_fact_fields
                    ]
                    or ["案件材料是否足以支持确定性规则评估？"]
                ),
                required_fact_fields=list(request.required_fact_fields),
                planned_tools=tools or [request.available_tools[0]],
                evidence_gaps=gaps,
                completion_criteria=[
                    "所有 required_fact_fields 均为 confirmed",
                    "所有正式 Claim 均通过 Citation 校验",
                    "确定性规则评估已完成",
                ],
            ),
            token_usage=0,
        )


class LangChainEvidencePlanner:
    """通过 LangChain function calling 生成 EvidencePlan，再执行服务端白名单复核。"""

    def __init__(self, model: BaseChatModel) -> None:
        self._planner = model.with_structured_output(
            EvidencePlan,
            method="function_calling",
            include_raw=True,
        )

    def build_plan(self, request: EvidencePlanRequest) -> EvidencePlanResult:
        result = self._planner.invoke(
            [
                SystemMessage(content=_SYSTEM),
                HumanMessage(
                    content=json.dumps(request.model_dump(mode="json"), ensure_ascii=False)
                ),
            ]
        )
        if not isinstance(result, dict):
            raise ValueError("LangChain Evidence planner 未返回结构化结果")
        raw = result.get("raw")
        parsed = result.get("parsed")
        parsing_error = result.get("parsing_error")
        if parsing_error is not None:
            raise ValueError("LangChain Evidence planner 返回非法结构") from parsing_error
        try:
            plan = (
                parsed if isinstance(parsed, EvidencePlan) else EvidencePlan.model_validate(parsed)
            )
        except ValidationError as exc:
            raise ValueError("LangChain Evidence planner 返回非法结构") from exc
        return EvidencePlanResult(
            plan=_validate_scope(plan, request),
            token_usage=_token_usage(raw),
        )


def _validate_scope(
    plan: EvidencePlan,
    request: EvidencePlanRequest,
) -> EvidencePlan:
    unknown_tools = sorted(set(plan.planned_tools) - set(request.available_tools))
    if unknown_tools:
        raise ValueError("Evidence planner 返回未授权工具: " + ", ".join(unknown_tools))
    unknown_fields = sorted(set(plan.required_fact_fields) - set(request.required_fact_fields))
    if unknown_fields:
        raise ValueError("Evidence planner 扩大了事实字段白名单: " + ", ".join(unknown_fields))
    omitted_fields = sorted(set(request.required_fact_fields) - set(plan.required_fact_fields))
    if omitted_fields:
        raise ValueError("Evidence planner 省略了必需事实字段: " + ", ".join(omitted_fields))
    missing_mandatory = sorted(_MANDATORY_TOOLS - set(plan.planned_tools))
    if missing_mandatory:
        raise ValueError("Evidence planner 省略了强制门禁工具: " + ", ".join(missing_mandatory))
    return plan


def _token_usage(raw: object) -> int:
    if not isinstance(raw, AIMessage) or raw.usage_metadata is None:
        return 0
    value = raw.usage_metadata.get("total_tokens", 0)
    return value if isinstance(value, int) and value >= 0 else 0
