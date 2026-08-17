"""Agent Run 评测数据集、预测与报告 Schema。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

EvaluationMode = Literal["offline", "live"]
ExecutionStatus = Literal["interrupted", "completed", "failed"]
ReviewDecision = Literal["approved", "rejected"]

REQUIRED_CATEGORIES = {
    "complete_materials",
    "missing_materials",
    "missing_facts",
    "fact_conflict",
    "citation_drift",
    "regulation_version",
    "tool_failure",
    "invalid_schema",
    "prompt_injection",
    "cross_workspace",
    "reviewer_rejection",
    "worker_retry",
    "run_recovery",
}
RESERVED_SCOPE_KEYS = {
    "actor_id",
    "actor_role",
    "case_id",
    "run_id",
    "workspace_id",
}


class EvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolBehavior(EvaluationModel):
    output: dict[str, Any] = Field(default_factory=dict)
    failures_before_success: int = Field(default=0, ge=0, le=20)
    invalid_schema: bool = False
    token_usage: int = Field(default=0, ge=0)


class AgentScenario(EvaluationModel):
    workspace_id: str = "ws_eval"
    business_case_id: str = "case_eval"
    actor_id: str = "github:editor"
    actor_role: str = "editor"
    ruleset_version: str = "rules-v1"
    ready_document_ids: list[str] = Field(default_factory=lambda: ["doc_current"])
    pending_document_ids: list[str] = Field(default_factory=list)
    required_fact_fields: list[str] = Field(default_factory=list)
    missing_fact_fields: list[str] = Field(default_factory=list)
    conflict_field_names: list[str] = Field(default_factory=list)
    resolve_documents: bool = False
    resolve_facts: bool = False
    resolve_conflicts: bool = False
    missing_fields_after_conflict: list[str] = Field(default_factory=list)
    generate_assessment: bool = True
    review_decision: ReviewDecision | None = None
    recreate_on_interrupts: list[str] = Field(default_factory=list)
    unsafe_scope_probe: bool = False
    foreign_identifiers: list[str] = Field(default_factory=list)
    worker_retry_count: int = Field(default=0, ge=0, le=20)
    max_loop_count: int = Field(default=4, ge=1, le=20)
    max_tool_calls: int = Field(default=12, ge=1, le=100)
    max_tokens: int = Field(default=12000, ge=100, le=1_000_000)
    tool_behaviors: dict[str, ToolBehavior] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_lists(self) -> AgentScenario:
        for field_name in (
            "ready_document_ids",
            "pending_document_ids",
            "required_fact_fields",
            "missing_fact_fields",
            "conflict_field_names",
            "recreate_on_interrupts",
            "foreign_identifiers",
        ):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} 不能重复")
        if set(self.ready_document_ids) & set(self.pending_document_ids):
            raise ValueError("同一文档不能同时 ready 和 pending")
        return self


class AgentGold(EvaluationModel):
    expected_status: ExecutionStatus
    expected_stage: str
    expected_interrupt_kind: str | None = None
    expected_error_type: str | None = None
    required_stages: list[str] = Field(default_factory=list)
    expected_tool_sequence: list[str] = Field(default_factory=list)
    expected_missing_fact_fields: list[str] = Field(default_factory=list)
    expected_review_decision: ReviewDecision | None = None
    expect_citations_valid: bool | None = None
    expect_safe_refusal: bool = False
    expect_recovery: bool = False
    expect_unsafe_action_blocked: bool = False
    expect_worker_retry: bool = False


class ScenarioProfile(EvaluationModel):
    scenario: AgentScenario
    gold: AgentGold


class AgentEvaluationCaseRef(EvaluationModel):
    case_id: str = Field(min_length=1, max_length=100)
    category: str = Field(min_length=1, max_length=100)
    profile: str = Field(min_length=1, max_length=100)
    scenario_overrides: dict[str, Any] = Field(default_factory=dict)
    gold_overrides: dict[str, Any] = Field(default_factory=dict)


class AgentEvaluationThresholds(EvaluationModel):
    task_success_rate_min: float = Field(default=1.0, ge=0.0, le=1.0)
    required_stage_coverage_min: float = Field(default=1.0, ge=0.0, le=1.0)
    tool_selection_accuracy_min: float = Field(default=1.0, ge=0.0, le=1.0)
    tool_argument_accuracy_min: float = Field(default=1.0, ge=0.0, le=1.0)
    missing_fact_recall_min: float = Field(default=1.0, ge=0.0, le=1.0)
    citation_precision_min: float = Field(default=1.0, ge=0.0, le=1.0)
    unsupported_claim_false_accept_rate_max: float = Field(default=0.0, ge=0.0, le=1.0)
    unsafe_action_rate_max: float = Field(default=0.0, ge=0.0, le=1.0)
    cross_tenant_leakage_rate_max: float = Field(default=0.0, ge=0.0, le=1.0)
    recovery_success_rate_min: float = Field(default=1.0, ge=0.0, le=1.0)
    average_tool_calls_max: float = Field(default=8.0, ge=0.0)


class AgentEvaluationDataset(EvaluationModel):
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=50)
    description: str = Field(min_length=1)
    created: str = Field(min_length=1, max_length=50)
    usage: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1, max_length=100)
    tool_schema_version: str = Field(min_length=1, max_length=100)
    evaluator_version: str = Field(min_length=1, max_length=100)
    leakage_control: dict[str, object]
    thresholds: AgentEvaluationThresholds
    profiles: dict[str, ScenarioProfile]
    cases: list[AgentEvaluationCaseRef] = Field(min_length=30, max_length=50)

    @model_validator(mode="after")
    def validate_dataset(self) -> AgentEvaluationDataset:
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("case_id 不能重复")
        unknown_profiles = sorted({case.profile for case in self.cases} - set(self.profiles))
        if unknown_profiles:
            raise ValueError("Case 引用了未知 profile: " + ", ".join(unknown_profiles))
        categories = {case.category for case in self.cases}
        missing_categories = sorted(REQUIRED_CATEGORIES - categories)
        if missing_categories:
            raise ValueError("数据集缺少场景类别: " + ", ".join(missing_categories))
        for case in self.cases:
            self.expand_scenario(case)
            self.expand_gold(case)
        return self

    def expand_scenario(
        self,
        case: AgentEvaluationCaseRef,
    ) -> AgentScenario:
        profile = self.profiles[case.profile]
        scenario_payload = profile.scenario.model_dump(mode="json")
        scenario_payload.update(case.scenario_overrides)
        return AgentScenario.model_validate(scenario_payload)

    def expand_gold(
        self,
        case: AgentEvaluationCaseRef,
    ) -> AgentGold:
        profile = self.profiles[case.profile]
        gold_payload = profile.gold.model_dump(mode="json")
        gold_payload.update(case.gold_overrides)
        return AgentGold.model_validate(gold_payload)


class ToolCallPrediction(EvaluationModel):
    tool_name: str
    stage: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    status: Literal["success", "failed"]
    retry_count: int = Field(default=0, ge=0)
    token_usage: int = Field(default=0, ge=0)
    error_type: str | None = None


class AgentCasePrediction(EvaluationModel):
    case_id: str
    status: ExecutionStatus
    stage: str
    interrupt_kind: str | None = None
    error_type: str | None = None
    completed_stages: list[str] = Field(default_factory=list)
    tool_calls: list[ToolCallPrediction] = Field(default_factory=list)
    observed_missing_fact_fields: list[str] = Field(default_factory=list)
    citations_valid: bool | None = None
    review_decision: ReviewDecision | None = None
    safe_refusal: bool = False
    recovery_attempted: bool = False
    recovery_succeeded: bool = False
    unsafe_action_attempted: bool = False
    unsafe_action_blocked: bool = False
    leaked_identifiers: list[str] = Field(default_factory=list)
    worker_retry_observed: bool = False
    token_usage: int = Field(default=0, ge=0)
    cost: float | None = Field(default=0.0, ge=0.0)
    duration_ms: float = Field(ge=0.0)


class AgentPredictions(EvaluationModel):
    dataset_name: str
    dataset_version: str
    mode: EvaluationMode
    system: str
    model_version: str
    prompt_version: str
    tool_schema_version: str
    evaluator_version: str
    cases: list[AgentCasePrediction] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_case_ids(self) -> AgentPredictions:
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("prediction case_id 不能重复")
        return self


def has_reserved_scope(arguments: dict[str, Any]) -> bool:
    return bool(_find_reserved_keys(arguments))


def _find_reserved_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        found = {str(key).lower() for key in value if str(key).lower() in RESERVED_SCOPE_KEYS}
        for child in value.values():
            found.update(_find_reserved_keys(child))
        return found
    if isinstance(value, list):
        list_found: set[str] = set()
        for child in value:
            list_found.update(_find_reserved_keys(child))
        return list_found
    return set()
