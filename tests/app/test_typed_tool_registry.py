"""TypedToolRegistry scope 注入、Schema 与 Policy 测试。"""

from __future__ import annotations

import time

import pytest
from pydantic import BaseModel, ConfigDict, Field

from app.agent_tools import RegisteredTool, TypedToolRegistry
from domain import AgentRuntimeContext, CaseAssessmentToolPort


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)


class SearchOutput(BaseModel):
    document_ids: list[str]
    injected_case_id: str


def _context(*, role: str = "editor", stage: str = "retrieve_case_evidence") -> AgentRuntimeContext:
    return AgentRuntimeContext(
        run_id="run_001",
        workspace_id="ws_001",
        case_id="case_001",
        actor_id="github:alice",
        actor_role=role,
        workflow_stage=stage,
    )


def _registry() -> TypedToolRegistry:
    registry = TypedToolRegistry()
    registry.register(
        RegisteredTool(
            name="retrieve_case_evidence",
            description="检索当前案件证据",
            input_model=SearchInput,
            output_model=SearchOutput,
            executor=lambda args, context: SearchOutput(
                document_ids=[args.query],
                injected_case_id=context.case_id,
            ),
            timeout_seconds=5.0,
            max_retries=1,
            required_roles=frozenset({"editor", "reviewer", "admin"}),
            allowed_stages=frozenset({"retrieve_case_evidence"}),
            side_effect_level="read_only",
        )
    )
    return registry


def test_registry_satisfies_port_and_injects_scope() -> None:
    registry = _registry()

    result = registry.execute(
        "retrieve_case_evidence",
        {"query": "重要数据", "top_k": 3},
        context=_context(),
    )

    assert isinstance(registry, CaseAssessmentToolPort)
    assert result.output["injected_case_id"] == "case_001"
    assert result.arguments == {
        "query": "[redacted]",
        "query_length": 4,
        "top_k": 3,
    }
    assert "case_id" not in result.arguments
    assert registry.definitions()[0].side_effect_level == "read_only"


def test_model_cannot_supply_case_scope_field() -> None:
    with pytest.raises(PermissionError, match="scope"):
        _registry().execute(
            "retrieve_case_evidence",
            {"query": "重要数据", "case_id": "case_other"},
            context=_context(),
        )


@pytest.mark.parametrize(
    ("role", "stage"),
    [("viewer", "retrieve_case_evidence"), ("editor", "draft_findings")],
)
def test_registry_rejects_wrong_role_or_stage(role: str, stage: str) -> None:
    with pytest.raises(PermissionError):
        _registry().execute(
            "retrieve_case_evidence",
            {"query": "重要数据"},
            context=_context(role=role, stage=stage),
        )


def test_registry_retries_transient_failure_and_records_retry_count() -> None:
    attempts = 0

    def flaky(args: SearchInput, context: AgentRuntimeContext) -> SearchOutput:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary")
        return SearchOutput(
            document_ids=[args.query],
            injected_case_id=context.case_id,
        )

    registry = TypedToolRegistry()
    registry.register(
        RegisteredTool(
            name="retryable",
            description="可重试读取",
            input_model=SearchInput,
            output_model=SearchOutput,
            executor=flaky,
            timeout_seconds=1.0,
            max_retries=1,
            required_roles=frozenset({"editor"}),
            allowed_stages=frozenset({"retrieve_case_evidence"}),
            side_effect_level="read_only",
        )
    )

    result = registry.execute(
        "retryable",
        {"query": "重要数据"},
        context=_context(),
    )

    assert attempts == 2
    assert result.retry_count == 1


def test_registry_timeout_fails_closed() -> None:
    def slow(args: SearchInput, context: AgentRuntimeContext) -> SearchOutput:
        time.sleep(0.05)
        return SearchOutput(
            document_ids=[args.query],
            injected_case_id=context.case_id,
        )

    registry = TypedToolRegistry()
    registry.register(
        RegisteredTool(
            name="slow",
            description="超时读取",
            input_model=SearchInput,
            output_model=SearchOutput,
            executor=slow,
            timeout_seconds=0.01,
            max_retries=0,
            required_roles=frozenset({"editor"}),
            allowed_stages=frozenset({"retrieve_case_evidence"}),
            side_effect_level="read_only",
        )
    )

    with pytest.raises(TimeoutError, match="超过"):
        registry.execute(
            "slow",
            {"query": "重要数据"},
            context=_context(),
        )
