"""V2 确定性规则引擎。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from domain.facts import CaseFact
from domain.policies import (
    PolicyEvaluation,
    PolicyEvaluationReport,
    PolicyRule,
)

_LEAF_KEYS = {"field", "operator", "value"}
_OPERATORS = {"eq", "ne", "gt", "gte", "lt", "lte", "in", "contains"}


class PolicyRuleEngine:
    """只消费 confirmed 事实的纯函数规则引擎。"""

    def evaluate(
        self,
        *,
        rules: list[PolicyRule],
        facts: list[CaseFact],
        assessment_date: date,
        jurisdiction: str,
        ruleset_version: str,
    ) -> PolicyEvaluationReport:
        applicable_rules = [
            rule
            for rule in rules
            if rule.status == "published"
            and rule.ruleset_version == ruleset_version
            and rule.jurisdiction == jurisdiction
            and rule.is_effective_on(assessment_date)
        ]
        confirmed_facts = [fact for fact in facts if fact.usable_for_rules]
        field_names = [fact.field_name for fact in confirmed_facts]
        duplicate_fields = sorted(
            field_name for field_name in set(field_names) if field_names.count(field_name) > 1
        )
        if duplicate_fields:
            fields = ", ".join(duplicate_fields)
            raise ValueError(f"同一字段存在多个 confirmed 事实: {fields}")
        confirmed = {fact.field_name: fact for fact in facts if fact.usable_for_rules}
        evaluations = [self._evaluate_rule(rule, confirmed) for rule in applicable_rules]
        return PolicyEvaluationReport(
            ruleset_version=ruleset_version,
            jurisdiction=jurisdiction,
            assessment_date=assessment_date,
            evaluations=evaluations,
        )

    def validate_rule(self, rule: PolicyRule) -> None:
        referenced = _validate_condition(rule.condition)
        undeclared = referenced - set(rule.required_fact_fields)
        if undeclared:
            fields = ", ".join(sorted(undeclared))
            raise ValueError(f"condition 引用了未声明的 required fact: {fields}")

    def _evaluate_rule(
        self,
        rule: PolicyRule,
        confirmed: Mapping[str, CaseFact],
    ) -> PolicyEvaluation:
        self.validate_rule(rule)
        missing = sorted(field for field in rule.required_fact_fields if field not in confirmed)
        if missing:
            return PolicyEvaluation(
                rule_id=rule.rule_id,
                ruleset_version=rule.ruleset_version,
                status="missing_facts",
                missing_fact_fields=missing,
                source_clause_ids=list(rule.source_clause_ids),
            )
        values = {field: confirmed[field].value for field in rule.required_fact_fields}
        consumed_versions = {field: confirmed[field].version for field in rule.required_fact_fields}
        triggered = _evaluate_condition(rule.condition, values)
        return PolicyEvaluation(
            rule_id=rule.rule_id,
            ruleset_version=rule.ruleset_version,
            status="triggered" if triggered else "not_triggered",
            consumed_fact_versions=consumed_versions,
            result=dict(rule.result) if triggered else {},
            source_clause_ids=list(rule.source_clause_ids),
        )


def _validate_condition(condition: Any) -> set[str]:
    if not isinstance(condition, dict) or not condition:
        raise ValueError("condition 必须是非空对象")
    keys = set(condition)
    compound_keys = keys & {"all", "any", "not"}
    if compound_keys:
        if len(compound_keys) != 1 or len(keys) != 1:
            raise ValueError("组合 condition 只能包含 all/any/not 中的一个")
        key = next(iter(compound_keys))
        value = condition[key]
        if key in {"all", "any"}:
            if not isinstance(value, list) or not value:
                raise ValueError(f"{key} 必须是非空数组")
            referenced: set[str] = set()
            for child in value:
                referenced.update(_validate_condition(child))
            return referenced
        return _validate_condition(value)
    if keys != _LEAF_KEYS:
        raise ValueError("叶子 condition 必须且只能包含 field/operator/value")
    field = condition["field"]
    operator = condition["operator"]
    if not isinstance(field, str) or not field.strip():
        raise ValueError("condition.field 必须是非空字符串")
    if operator not in _OPERATORS:
        raise ValueError(f"不支持的 operator: {operator!r}")
    return {field}


def _evaluate_condition(condition: dict[str, Any], values: Mapping[str, Any]) -> bool:
    if "all" in condition:
        return all(_evaluate_condition(child, values) for child in condition["all"])
    if "any" in condition:
        return any(_evaluate_condition(child, values) for child in condition["any"])
    if "not" in condition:
        return not _evaluate_condition(condition["not"], values)
    actual = values[condition["field"]]
    expected = condition["value"]
    operator = condition["operator"]
    try:
        if operator == "eq":
            return bool(actual == expected)
        if operator == "ne":
            return bool(actual != expected)
        if operator == "gt":
            return bool(actual > expected)
        if operator == "gte":
            return bool(actual >= expected)
        if operator == "lt":
            return bool(actual < expected)
        if operator == "lte":
            return bool(actual <= expected)
        if operator == "in":
            return bool(actual in expected)
        if operator == "contains":
            return bool(expected in actual)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"字段 {condition['field']!r} 无法执行操作 {operator!r}") from exc
    raise ValueError(f"不支持的 operator: {operator!r}")
