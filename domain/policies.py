"""V2 版本化合规规则领域模型。"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import Field, model_validator

from domain.models import BaseDomainModel

PolicyRuleStatus = Literal["draft", "published", "retired"]
PolicyEvaluationStatus = Literal["triggered", "not_triggered", "missing_facts"]


class PolicyRule(BaseDomainModel):
    """可追溯到法规条款的确定性规则。"""

    rule_id: str = Field(min_length=1)
    ruleset_version: str = Field(min_length=1, max_length=100)
    jurisdiction: str = Field(min_length=1, max_length=32)
    effective_from: date
    effective_to: date | None = None
    status: PolicyRuleStatus = "draft"
    required_fact_fields: list[str] = Field(default_factory=list)
    condition: dict[str, Any]
    result: dict[str, Any] = Field(default_factory=dict)
    source_clause_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_rule(self) -> PolicyRule:
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to 不能早于 effective_from")
        if len(self.required_fact_fields) != len(set(self.required_fact_fields)):
            raise ValueError("required_fact_fields 不能重复")
        if any(not field.strip() for field in self.required_fact_fields):
            raise ValueError("required_fact_fields 不能包含空值")
        if len(self.source_clause_ids) != len(set(self.source_clause_ids)):
            raise ValueError("source_clause_ids 不能重复")
        return self

    def is_effective_on(self, assessment_date: date) -> bool:
        return self.effective_from <= assessment_date and (
            self.effective_to is None or assessment_date <= self.effective_to
        )


class PolicyEvaluation(BaseDomainModel):
    """单条规则对某案件事实快照的计算结果。"""

    rule_id: str
    ruleset_version: str
    status: PolicyEvaluationStatus
    missing_fact_fields: list[str] = Field(default_factory=list)
    consumed_fact_versions: dict[str, int] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    source_clause_ids: list[str] = Field(default_factory=list)


class PolicyEvaluationReport(BaseDomainModel):
    """一组规则的确定性评估报告。"""

    ruleset_version: str
    jurisdiction: str
    assessment_date: date
    evaluations: list[PolicyEvaluation]

    @property
    def triggered(self) -> list[PolicyEvaluation]:
        return [evaluation for evaluation in self.evaluations if evaluation.status == "triggered"]

    @property
    def missing_fact_fields(self) -> list[str]:
        return sorted(
            {field for evaluation in self.evaluations for field in evaluation.missing_fact_fields}
        )
