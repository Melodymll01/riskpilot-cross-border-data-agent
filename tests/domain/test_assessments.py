"""V2 Assessment、Finding 与 ActionItem 测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from domain import (
    ActionItem,
    Assessment,
    AssessmentBundle,
    Finding,
    InvalidAssessmentTransition,
    PolicyEvaluation,
)


def _assessment(**overrides: object) -> Assessment:
    values: dict[str, object] = {
        "assessment_id": "assessment_001",
        "case_id": "case_001",
        "version": 1,
        "assessment_date": "2026-08-06",
        "jurisdiction": "CN",
        "ruleset_version": "synthetic-v1",
        "fact_versions": {"flag": 1},
        "policy_evaluations": [
            PolicyEvaluation(
                rule_id="rule_001",
                ruleset_version="synthetic-v1",
                status="triggered",
                consumed_fact_versions={"flag": 1},
                result={"risk_level": "high"},
                source_clause_ids=["clause_001"],
            )
        ],
        "risk_level": "high",
        "candidate_paths": ["synthetic"],
        "created_at": 100.0,
        "updated_at": 100.0,
    }
    values.update(overrides)
    return Assessment(**values)  # type: ignore[arg-type]


def _finding(**overrides: object) -> Finding:
    values: dict[str, object] = {
        "finding_id": "finding_001",
        "assessment_id": "assessment_001",
        "finding_type": "rule_trigger",
        "severity": "high",
        "title": "触发合成规则",
        "rule_ids": ["rule_001"],
        "clause_ids": ["clause_001"],
    }
    values.update(overrides)
    return Finding(**values)  # type: ignore[arg-type]


class TestAssessment:
    def test_draft_to_approved_path(self) -> None:
        draft = _assessment()
        review = draft.transition_to(
            "review_required",
            actor_id="github:editor",
            at=101.0,
        )
        approved = review.transition_to(
            "approved",
            actor_id="github:reviewer",
            comment="审核通过",
            at=102.0,
        )
        assert approved.status == "approved"
        assert approved.approved_by == "github:reviewer"
        assert approved.approved_at == 102.0
        assert approved.review_comment == "审核通过"

    def test_approved_can_only_be_superseded(self) -> None:
        approved = _assessment(
            status="approved",
            approved_by="github:reviewer",
            approved_at=100.0,
        )
        with pytest.raises(InvalidAssessmentTransition):
            approved.transition_to(
                "review_required",
                actor_id="github:editor",
                at=101.0,
            )
        superseded = approved.transition_to(
            "superseded",
            actor_id="system:versioning",
            at=101.0,
        )
        assert superseded.status == "superseded"
        assert superseded.approved_by is None

    def test_direct_draft_approve_rejected(self) -> None:
        with pytest.raises(InvalidAssessmentTransition):
            _assessment().transition_to(
                "approved",
                actor_id="github:reviewer",
                at=101.0,
            )

    def test_approved_constructor_requires_metadata(self) -> None:
        with pytest.raises(ValidationError, match="审批"):
            _assessment(status="approved")

    def test_fact_versions_must_be_positive(self) -> None:
        with pytest.raises(ValidationError, match="fact_versions"):
            _assessment(fact_versions={"flag": 0})

    def test_json_round_trip(self) -> None:
        assessment = _assessment()
        assert Assessment.model_validate_json(assessment.model_dump_json()) == assessment


class TestFindingAndAction:
    def test_bundle_happy_path(self) -> None:
        finding = _finding()
        action = ActionItem(
            action_id="action_001",
            assessment_id="assessment_001",
            title="补充材料",
            priority="high",
            related_finding_ids=[finding.finding_id],
        )
        bundle = AssessmentBundle(
            assessment=_assessment(),
            findings=[finding],
            action_items=[action],
        )
        assert bundle.action_items[0].priority == "high"

    def test_duplicate_finding_references_rejected(self) -> None:
        with pytest.raises(ValidationError, match="重复"):
            _finding(rule_ids=["rule_001", "rule_001"])

    def test_cross_assessment_finding_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Finding"):
            AssessmentBundle(
                assessment=_assessment(),
                findings=[_finding(assessment_id="assessment_other")],
            )

    def test_action_missing_finding_rejected(self) -> None:
        with pytest.raises(ValidationError, match="不存在"):
            AssessmentBundle(
                assessment=_assessment(),
                action_items=[
                    ActionItem(
                        action_id="action_001",
                        assessment_id="assessment_001",
                        title="补充材料",
                        priority="high",
                        related_finding_ids=["missing"],
                    )
                ],
            )
