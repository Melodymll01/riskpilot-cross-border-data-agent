"""核心 Agent 计划与预算领域模型测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from domain import AgentBudget, EvidencePlan


def test_evidence_plan_requires_unique_non_blank_fields() -> None:
    plan = EvidencePlan(
        investigation_questions=["是否涉及重要数据"],
        required_fact_fields=["important_data_involved"],
        planned_tools=["retrieve_case_evidence"],
        evidence_gaps=["缺少数据分类证明"],
        completion_criteria=["关键事实均 confirmed"],
    )

    assert plan.required_fact_fields == ["important_data_involved"]

    with pytest.raises(ValidationError, match="不能重复"):
        EvidencePlan(
            investigation_questions=["问题", "问题"],
            required_fact_fields=["field"],
            planned_tools=["tool"],
            completion_criteria=["done"],
        )


def test_agent_budget_fails_closed_at_loop_tool_and_token_limits() -> None:
    budget = AgentBudget(max_loop_count=1, max_tool_calls=1, max_tokens=100)

    consumed = budget.consume_loop().consume_tool(tokens=100)

    assert consumed.exhausted is True
    with pytest.raises(ValueError, match="loop budget"):
        consumed.consume_loop()
    with pytest.raises(ValueError, match="tool-call budget"):
        consumed.consume_tool()
