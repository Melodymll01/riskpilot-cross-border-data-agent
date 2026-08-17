"""Case Assessment Agent 的受控业务工具集合。"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from app.agent_tools.registry import RegisteredTool, TypedToolRegistry

if TYPE_CHECKING:
    from app.use_cases.assessment_management import AssessmentManagementUseCase
    from app.use_cases.evidence_search import EvidenceSearchUseCase
    from app.use_cases.fact_management import FactManagementUseCase
    from app.use_cases.policy_management import PolicyManagementUseCase
    from domain.agent_workflow import AgentRuntimeContext
    from domain.ports import MetricsPort, TracePort

_AGENT_ROLES = frozenset({"editor", "reviewer", "admin"})
CASE_ASSESSMENT_TOOL_SCHEMA_VERSION = "case-assessment-tools-v1"


class RetrieveCaseEvidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)


class RetrieveCaseEvidenceOutput(BaseModel):
    evidence_ids: list[str]
    document_ids: list[str]
    document_version_ids: list[str]
    hit_count: int = Field(ge=0)


class RetrieveRegulationsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ruleset_version: str = Field(min_length=1, max_length=100)


class RetrieveRegulationsOutput(BaseModel):
    rule_ids: list[str]
    required_fact_fields: list[str]
    source_clause_ids: list[str]


class ExtractFactCandidatesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_names: list[str] = Field(min_length=1, max_length=20)
    document_ids: list[str] | None = Field(default=None, min_length=1, max_length=20)


class ExtractFactCandidatesOutput(BaseModel):
    fact_ids: list[str]
    proposed_field_names: list[str]
    conflict_field_names: list[str]
    input_tokens: int = Field(default=0, ge=0, exclude=True)
    output_tokens: int = Field(default=0, ge=0, exclude=True)
    token_usage: int = Field(default=0, ge=0, exclude=True)


class EvaluateRulesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ruleset_version: str = Field(min_length=1, max_length=100)


class EvaluateRulesOutput(BaseModel):
    triggered_rule_ids: list[str]
    missing_fact_fields: list[str]
    evaluation_status_by_rule: dict[str, str]


class VerifyCitationsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessment_id: str = Field(min_length=1)


class VerifyCitationsOutput(BaseModel):
    assessment_id: str
    valid: bool
    citation_count: int = Field(ge=0)
    finding_count: int = Field(ge=0)


def build_case_assessment_tool_registry(
    *,
    evidence_search: EvidenceSearchUseCase,
    policy_management: PolicyManagementUseCase,
    fact_management: FactManagementUseCase,
    assessment_management: AssessmentManagementUseCase,
    trace: TracePort | None = None,
    metrics: MetricsPort | None = None,
    model_name: str = "unconfigured",
    input_cost_per_1m_tokens: float = 0.0,
    output_cost_per_1m_tokens: float = 0.0,
) -> TypedToolRegistry:
    registry = TypedToolRegistry(
        trace=trace,
        metrics=metrics,
        model_name=model_name,
        input_cost_per_1m_tokens=input_cost_per_1m_tokens,
        output_cost_per_1m_tokens=output_cost_per_1m_tokens,
    )

    def retrieve_case(
        args: RetrieveCaseEvidenceInput,
        context: AgentRuntimeContext,
    ) -> RetrieveCaseEvidenceOutput:
        hits = evidence_search.search(
            context.actor_id,
            case_id=context.case_id,
            query=args.query,
            top_k=args.top_k,
        )
        return RetrieveCaseEvidenceOutput(
            evidence_ids=[hit.chunk.chunk_id for hit in hits],
            document_ids=_unique(hit.chunk.document_id for hit in hits),
            document_version_ids=_unique(hit.chunk.document_version_id for hit in hits),
            hit_count=len(hits),
        )

    registry.register(
        RegisteredTool(
            name="retrieve_case_evidence",
            description="按当前 Case scope 检索当前版本案件证据",
            input_model=RetrieveCaseEvidenceInput,
            output_model=RetrieveCaseEvidenceOutput,
            executor=retrieve_case,
            timeout_seconds=15.0,
            max_retries=1,
            required_roles=_AGENT_ROLES,
            allowed_stages=frozenset({"retrieve_case_evidence"}),
            side_effect_level="read_only",
        )
    )

    def retrieve_regulations(
        args: RetrieveRegulationsInput,
        context: AgentRuntimeContext,
    ) -> RetrieveRegulationsOutput:
        rules = policy_management.list_rules(
            context.workspace_id,
            context.actor_id,
            ruleset_version=args.ruleset_version,
            status="published",
        )
        return RetrieveRegulationsOutput(
            rule_ids=[rule.rule_id for rule in rules],
            required_fact_fields=sorted(
                {field_name for rule in rules for field_name in rule.required_fact_fields}
            ),
            source_clause_ids=sorted(
                {clause_id for rule in rules for clause_id in rule.source_clause_ids}
            ),
        )

    registry.register(
        RegisteredTool(
            name="retrieve_regulations",
            description="读取当前 Workspace 已发布规则快照元数据",
            input_model=RetrieveRegulationsInput,
            output_model=RetrieveRegulationsOutput,
            executor=retrieve_regulations,
            timeout_seconds=10.0,
            max_retries=1,
            required_roles=_AGENT_ROLES,
            allowed_stages=frozenset({"retrieve_regulations"}),
            side_effect_level="read_only",
        )
    )

    def extract_facts(
        args: ExtractFactCandidatesInput,
        context: AgentRuntimeContext,
    ) -> ExtractFactCandidatesOutput:
        batch = fact_management.propose_from_documents(
            context.actor_id,
            case_id=context.case_id,
            field_names=args.field_names,
            document_ids=args.document_ids,
            max_token_usage=context.remaining_token_budget,
        )
        return ExtractFactCandidatesOutput(
            fact_ids=[detail.fact.fact_id for detail in batch.facts],
            proposed_field_names=[detail.fact.field_name for detail in batch.facts],
            conflict_field_names=list(batch.conflict_field_names),
            input_tokens=batch.input_tokens,
            output_tokens=batch.output_tokens,
            token_usage=batch.token_usage,
        )

    registry.register(
        RegisteredTool(
            name="extract_fact_candidates",
            description="从当前 Case 的 ready 文档提议待人工确认事实",
            input_model=ExtractFactCandidatesInput,
            output_model=ExtractFactCandidatesOutput,
            executor=extract_facts,
            timeout_seconds=60.0,
            max_retries=0,
            required_roles=_AGENT_ROLES,
            allowed_stages=frozenset({"extract_fact_candidates"}),
            side_effect_level="reversible_write",
        )
    )

    def evaluate_rules(
        args: EvaluateRulesInput,
        context: AgentRuntimeContext,
    ) -> EvaluateRulesOutput:
        report = policy_management.evaluate_case(
            context.case_id,
            context.actor_id,
            ruleset_version=args.ruleset_version,
        )
        return EvaluateRulesOutput(
            triggered_rule_ids=[evaluation.rule_id for evaluation in report.triggered],
            missing_fact_fields=list(report.missing_fact_fields),
            evaluation_status_by_rule={
                evaluation.rule_id: evaluation.status for evaluation in report.evaluations
            },
        )

    registry.register(
        RegisteredTool(
            name="evaluate_deterministic_rules",
            description="调用确定性 PolicyRuleEngine 计算合规路径",
            input_model=EvaluateRulesInput,
            output_model=EvaluateRulesOutput,
            executor=evaluate_rules,
            timeout_seconds=15.0,
            max_retries=0,
            required_roles=_AGENT_ROLES,
            allowed_stages=frozenset({"evaluate_deterministic_rules"}),
            side_effect_level="read_only",
        )
    )

    def verify_citations(
        args: VerifyCitationsInput,
        context: AgentRuntimeContext,
    ) -> VerifyCitationsOutput:
        bundle = assessment_management.verify_references(
            args.assessment_id,
            context.actor_id,
        )
        return VerifyCitationsOutput(
            assessment_id=bundle.assessment.assessment_id,
            valid=True,
            citation_count=len(bundle.evidence_citations),
            finding_count=len(bundle.findings),
        )

    registry.register(
        RegisteredTool(
            name="verify_claim_citations",
            description="重新读取 Assessment 的 Fact、原文与规则快照并校验引用",
            input_model=VerifyCitationsInput,
            output_model=VerifyCitationsOutput,
            executor=verify_citations,
            timeout_seconds=20.0,
            max_retries=0,
            required_roles=_AGENT_ROLES,
            allowed_stages=frozenset({"verify_claim_citations"}),
            side_effect_level="read_only",
        )
    )
    return registry


def _unique(values: Iterable[object]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values))
