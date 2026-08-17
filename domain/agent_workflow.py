"""核心案件 Agent 的结构化计划、工具与预算契约。"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, StringConstraints, model_validator

from domain.models import BaseDomainModel

ToolSideEffectLevel = Literal[
    "read_only",
    "reversible_write",
    "privileged_write",
    "forbidden_for_agent",
]
PlanText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
FactFieldName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
]
ToolName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class EvidencePlan(BaseDomainModel):
    investigation_questions: list[PlanText] = Field(min_length=1, max_length=20)
    required_fact_fields: list[FactFieldName] = Field(default_factory=list, max_length=50)
    planned_tools: list[ToolName] = Field(min_length=1, max_length=20)
    evidence_gaps: list[PlanText] = Field(default_factory=list, max_length=50)
    completion_criteria: list[PlanText] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_plan(self) -> EvidencePlan:
        for name, values in (
            ("investigation_questions", self.investigation_questions),
            ("required_fact_fields", self.required_fact_fields),
            ("planned_tools", self.planned_tools),
            ("evidence_gaps", self.evidence_gaps),
            ("completion_criteria", self.completion_criteria),
        ):
            if any(not value.strip() for value in values):
                raise ValueError(f"{name} 不能包含空白字符串")
            if len(values) != len(set(values)):
                raise ValueError(f"{name} 不能重复")
        return self


class EvidencePlanResult(BaseDomainModel):
    plan: EvidencePlan
    token_usage: int = Field(default=0, ge=0)


class AgentRuntimeContext(BaseDomainModel):
    run_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    actor_role: str = Field(min_length=1)
    workflow_stage: str = Field(min_length=1)
    remaining_token_budget: int = Field(default=0, ge=0)


class ToolDefinition(BaseDomainModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    input_schema_name: str = Field(min_length=1, max_length=200)
    output_schema_name: str = Field(min_length=1, max_length=200)
    timeout_seconds: float = Field(gt=0.0, le=300.0)
    max_retries: int = Field(default=0, ge=0, le=5)
    required_roles: list[str] = Field(min_length=1)
    allowed_stages: list[str] = Field(min_length=1)
    side_effect_level: ToolSideEffectLevel


class ToolExecutionResult(BaseDomainModel):
    tool_name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    result_summary: str = Field(default="", max_length=1000)
    duration_ms: int = Field(ge=0)
    retry_count: int = Field(default=0, ge=0)
    token_usage: int = Field(default=0, ge=0)


class AgentBudget(BaseDomainModel):
    max_loop_count: int = Field(default=4, ge=1, le=20)
    max_tool_calls: int = Field(default=12, ge=1, le=100)
    max_tokens: int = Field(default=12000, ge=100, le=1_000_000)
    loop_count: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    token_usage: int = Field(default=0, ge=0)

    @property
    def exhausted(self) -> bool:
        return (
            self.loop_count >= self.max_loop_count
            or self.tool_calls >= self.max_tool_calls
            or self.token_usage >= self.max_tokens
        )

    def consume_loop(self) -> AgentBudget:
        if self.loop_count >= self.max_loop_count:
            raise ValueError("Agent loop budget 已耗尽")
        return self.model_copy(update={"loop_count": self.loop_count + 1})

    def consume_tool(self, *, tokens: int = 0) -> AgentBudget:
        if self.tool_calls >= self.max_tool_calls:
            raise ValueError("Agent tool-call budget 已耗尽")
        if self.token_usage + tokens > self.max_tokens:
            raise ValueError("Agent token budget 已耗尽")
        return self.model_copy(
            update={
                "tool_calls": self.tool_calls + 1,
                "token_usage": self.token_usage + tokens,
            }
        )

    def consume_tokens(self, tokens: int) -> AgentBudget:
        if self.token_usage + tokens > self.max_tokens:
            raise ValueError("Agent token budget 已耗尽")
        return self.model_copy(update={"token_usage": self.token_usage + tokens})


class EvidencePlanRequest(BaseDomainModel):
    model_config = ConfigDict(extra="forbid")

    ruleset_version: str = Field(min_length=1)
    ready_document_count: int = Field(ge=0)
    required_fact_fields: list[str]
    available_tools: list[str]
