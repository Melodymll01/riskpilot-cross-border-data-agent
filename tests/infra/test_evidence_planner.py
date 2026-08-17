"""Evidence Planner 结构化与白名单测试。"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from domain import EvidencePlannerPort, EvidencePlanRequest
from infra.agents import (
    DeterministicEvidencePlanner,
    LangChainEvidencePlanner,
)
from tests.fakes import FakeToolCallingModel


def _request() -> EvidencePlanRequest:
    return EvidencePlanRequest(
        ruleset_version="rules-v1",
        ready_document_count=1,
        required_fact_fields=["important_data_involved"],
        available_tools=[
            "retrieve_case_evidence",
            "retrieve_regulations",
            "extract_fact_candidates",
            "evaluate_deterministic_rules",
            "verify_claim_citations",
        ],
    )


def test_deterministic_planner_satisfies_port_and_preserves_field_scope() -> None:
    planner = DeterministicEvidencePlanner()

    result = planner.build_plan(_request())

    assert isinstance(planner, EvidencePlannerPort)
    assert result.plan.required_fact_fields == ["important_data_involved"]
    assert "extract_fact_candidates" in result.plan.planned_tools
    assert result.token_usage == 0


def test_langchain_planner_rejects_unknown_tool_or_field() -> None:
    payload = {
        "investigation_questions": ["调查"],
        "required_fact_fields": ["outside_scope"],
        "planned_tools": ["delete_workspace"],
        "evidence_gaps": [],
        "completion_criteria": ["完成"],
    }
    model = FakeToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "EvidencePlan",
                        "args": payload,
                        "id": "plan_call_invalid",
                        "type": "tool_call",
                    }
                ],
            )
        ]
    )
    planner = LangChainEvidencePlanner(model)

    with pytest.raises(ValueError, match="未授权工具"):
        planner.build_plan(_request())


def test_langchain_planner_cannot_remove_deterministic_gates() -> None:
    payload = {
        "investigation_questions": ["调查"],
        "required_fact_fields": ["important_data_involved"],
        "planned_tools": ["retrieve_case_evidence", "extract_fact_candidates"],
        "evidence_gaps": [],
        "completion_criteria": ["完成"],
    }
    model = FakeToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "EvidencePlan",
                        "args": payload,
                        "id": "plan_call_missing_gates",
                        "type": "tool_call",
                    }
                ],
            )
        ]
    )

    with pytest.raises(ValueError, match="强制门禁工具"):
        LangChainEvidencePlanner(model).build_plan(_request())


def test_langchain_planner_uses_function_calling_and_revalidates_scope() -> None:
    payload = {
        "investigation_questions": ["是否涉及重要数据"],
        "required_fact_fields": ["important_data_involved"],
        "planned_tools": [
            "retrieve_case_evidence",
            "retrieve_regulations",
            "extract_fact_candidates",
            "evaluate_deterministic_rules",
            "verify_claim_citations",
        ],
        "evidence_gaps": [],
        "completion_criteria": ["关键事实已确认"],
    }
    model = FakeToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "EvidencePlan",
                        "args": payload,
                        "id": "plan_call_001",
                        "type": "tool_call",
                    }
                ],
                usage_metadata={
                    "input_tokens": 120,
                    "output_tokens": 40,
                    "total_tokens": 160,
                },
            )
        ]
    )

    result = LangChainEvidencePlanner(model).build_plan(_request())

    assert result.plan.required_fact_fields == ["important_data_involved"]
    assert result.token_usage == 160
    assert model.bound_tools
    assert model.calls
    serialized_messages = str(model.calls)
    assert "case_001" not in serialized_messages
    assert "doc_001" not in serialized_messages
