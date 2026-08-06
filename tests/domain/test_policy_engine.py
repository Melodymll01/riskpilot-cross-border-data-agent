"""V2 版本化确定性规则引擎测试。"""

from __future__ import annotations

from datetime import date

import pytest

from domain import CaseFact, PolicyRule, PolicyRuleEngine


def _confirmed_fact(
    field_name: str,
    value: object,
    *,
    version: int = 1,
) -> CaseFact:
    return CaseFact(
        fact_id=f"fact_{field_name}",
        case_id="case_001",
        field_name=field_name,
        value=value,  # type: ignore[arg-type]
        status="confirmed",
        source_type="user",
        confidence=1.0,
        version=version,
        created_by="github:editor",
        confirmed_by="github:reviewer",
        confirmed_at=101.0,
        created_at=100.0,
        updated_at=101.0,
    )


def _proposed_fact(field_name: str, value: object) -> CaseFact:
    return CaseFact(
        fact_id=f"fact_{field_name}",
        case_id="case_001",
        field_name=field_name,
        value=value,  # type: ignore[arg-type]
        source_type="document",
        confidence=0.9,
        created_by="system:extractor",
        created_at=100.0,
        updated_at=100.0,
    )


def _rule(**overrides: object) -> PolicyRule:
    values: dict[str, object] = {
        "rule_id": "PATH-SYNTHETIC-001",
        "ruleset_version": "synthetic-2026-01",
        "jurisdiction": "CN",
        "effective_from": date(2026, 1, 1),
        "status": "published",
        "required_fact_fields": [
            "important_data_involved",
            "subject_count",
        ],
        "condition": {
            "any": [
                {
                    "field": "important_data_involved",
                    "operator": "eq",
                    "value": True,
                },
                {
                    "field": "subject_count",
                    "operator": "gte",
                    "value": 100,
                },
            ]
        },
        "result": {
            "candidate_path": "synthetic_review",
            "risk_level": "high",
        },
        "source_clause_ids": ["synthetic-clause-1"],
    }
    values.update(overrides)
    return PolicyRule(**values)  # type: ignore[arg-type]


class TestPolicyRule:
    def test_effective_range(self) -> None:
        rule = _rule(effective_to=date(2026, 12, 31))
        assert rule.is_effective_on(date(2026, 6, 1)) is True
        assert rule.is_effective_on(date(2027, 1, 1)) is False

    def test_invalid_effective_range_rejected(self) -> None:
        with pytest.raises(ValueError, match="effective_to"):
            _rule(
                effective_from=date(2026, 2, 1),
                effective_to=date(2026, 1, 1),
            )


class TestPolicyRuleEngine:
    def test_triggered_rule_records_fact_versions_and_sources(self) -> None:
        report = PolicyRuleEngine().evaluate(
            rules=[_rule()],
            facts=[
                _confirmed_fact("important_data_involved", True, version=2),
                _confirmed_fact("subject_count", 10, version=3),
            ],
            assessment_date=date(2026, 8, 6),
            jurisdiction="CN",
            ruleset_version="synthetic-2026-01",
        )
        assert len(report.triggered) == 1
        evaluation = report.triggered[0]
        assert evaluation.result["candidate_path"] == "synthetic_review"
        assert evaluation.consumed_fact_versions == {
            "important_data_involved": 2,
            "subject_count": 3,
        }
        assert evaluation.source_clause_ids == ["synthetic-clause-1"]

    def test_missing_or_unconfirmed_facts_never_trigger(self) -> None:
        report = PolicyRuleEngine().evaluate(
            rules=[_rule()],
            facts=[
                _proposed_fact("important_data_involved", True),
                _confirmed_fact("subject_count", 10),
            ],
            assessment_date=date(2026, 8, 6),
            jurisdiction="CN",
            ruleset_version="synthetic-2026-01",
        )
        assert report.evaluations[0].status == "missing_facts"
        assert report.missing_fact_fields == ["important_data_involved"]
        assert report.triggered == []

    def test_not_triggered_has_no_result_payload(self) -> None:
        report = PolicyRuleEngine().evaluate(
            rules=[_rule()],
            facts=[
                _confirmed_fact("important_data_involved", False),
                _confirmed_fact("subject_count", 10),
            ],
            assessment_date=date(2026, 8, 6),
            jurisdiction="CN",
            ruleset_version="synthetic-2026-01",
        )
        evaluation = report.evaluations[0]
        assert evaluation.status == "not_triggered"
        assert evaluation.result == {}

    def test_filters_draft_wrong_version_jurisdiction_and_date(self) -> None:
        rules = [
            _rule(rule_id="draft", status="draft"),
            _rule(rule_id="wrong-version", ruleset_version="other"),
            _rule(rule_id="wrong-jurisdiction", jurisdiction="EU"),
            _rule(
                rule_id="expired",
                effective_to=date(2026, 1, 31),
            ),
            _rule(rule_id="active"),
        ]
        report = PolicyRuleEngine().evaluate(
            rules=rules,
            facts=[
                _confirmed_fact("important_data_involved", True),
                _confirmed_fact("subject_count", 10),
            ],
            assessment_date=date(2026, 8, 6),
            jurisdiction="CN",
            ruleset_version="synthetic-2026-01",
        )
        assert [evaluation.rule_id for evaluation in report.evaluations] == ["active"]

    @pytest.mark.parametrize(
        ("operator", "actual", "expected", "triggered"),
        [
            ("eq", "DE", "DE", True),
            ("ne", "DE", "US", True),
            ("gt", 11, 10, True),
            ("gte", 10, 10, True),
            ("lt", 9, 10, True),
            ("lte", 10, 10, True),
            ("in", "DE", ["DE", "FR"], True),
            ("contains", ["name", "email"], "email", True),
        ],
    )
    def test_leaf_operators(
        self,
        operator: str,
        actual: object,
        expected: object,
        triggered: bool,
    ) -> None:
        rule = _rule(
            required_fact_fields=["value"],
            condition={
                "field": "value",
                "operator": operator,
                "value": expected,
            },
        )
        report = PolicyRuleEngine().evaluate(
            rules=[rule],
            facts=[_confirmed_fact("value", actual)],
            assessment_date=date(2026, 8, 6),
            jurisdiction="CN",
            ruleset_version="synthetic-2026-01",
        )
        assert (report.evaluations[0].status == "triggered") is triggered

    def test_not_condition(self) -> None:
        rule = _rule(
            required_fact_fields=["flag"],
            condition={
                "not": {
                    "field": "flag",
                    "operator": "eq",
                    "value": True,
                }
            },
        )
        report = PolicyRuleEngine().evaluate(
            rules=[rule],
            facts=[_confirmed_fact("flag", False)],
            assessment_date=date(2026, 8, 6),
            jurisdiction="CN",
            ruleset_version="synthetic-2026-01",
        )
        assert report.evaluations[0].status == "triggered"

    def test_condition_must_only_reference_declared_fields(self) -> None:
        rule = _rule(
            required_fact_fields=["declared"],
            condition={
                "field": "undeclared",
                "operator": "eq",
                "value": True,
            },
        )
        with pytest.raises(ValueError, match="undeclared"):
            PolicyRuleEngine().validate_rule(rule)

    def test_incompatible_comparison_type_has_clear_error(self) -> None:
        rule = _rule(
            required_fact_fields=["value"],
            condition={
                "field": "value",
                "operator": "gte",
                "value": 10,
            },
        )
        with pytest.raises(ValueError, match="无法执行"):
            PolicyRuleEngine().evaluate(
                rules=[rule],
                facts=[_confirmed_fact("value", "not-a-number")],
                assessment_date=date(2026, 8, 6),
                jurisdiction="CN",
                ruleset_version="synthetic-2026-01",
            )

    @pytest.mark.parametrize(
        "condition",
        [
            {},
            {"all": []},
            {"any": []},
            {"field": "x", "operator": "unknown", "value": 1},
            {"all": [], "any": []},
        ],
    )
    def test_invalid_condition_schema_rejected(self, condition: dict) -> None:
        rule = _rule(required_fact_fields=["x"], condition=condition)
        with pytest.raises(ValueError):
            PolicyRuleEngine().validate_rule(rule)
