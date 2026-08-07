"""AssessmentManagementUseCase 确定性生成与版本测试。"""

from __future__ import annotations

from datetime import date

import pytest

from app.use_cases import (
    AssessmentManagementUseCase,
    CaseManagementUseCase,
    PolicyManagementUseCase,
    WorkspaceManagementUseCase,
)
from domain import CaseFact, PolicyRule
from tests.fakes import (
    InMemoryAssessmentRepo,
    InMemoryCaseFactRepo,
    InMemoryCaseRepo,
    InMemoryPolicyRuleRepo,
    InMemoryWorkspaceRepo,
)


def _setup():
    workspace_repo = InMemoryWorkspaceRepo()
    case_repo = InMemoryCaseRepo()
    fact_repo = InMemoryCaseFactRepo()
    rule_repo = InMemoryPolicyRuleRepo()
    assessment_repo = InMemoryAssessmentRepo(case_repo)
    workspace_uc = WorkspaceManagementUseCase(workspace_repo)
    case_uc = CaseManagementUseCase(
        case_repo=case_repo,
        workspace_repo=workspace_repo,
    )
    policy_uc = PolicyManagementUseCase(
        rule_repo=rule_repo,
        fact_repo=fact_repo,
        case_management=case_uc,
        workspace_management=workspace_uc,
    )
    assessment_uc = AssessmentManagementUseCase(
        assessment_repo=assessment_repo,
        fact_repo=fact_repo,
        case_management=case_uc,
        workspace_management=workspace_uc,
        policy_management=policy_uc,
    )
    workspace = workspace_uc.create_workspace("github:alice", name="跨境合规组")
    case = case_uc.create_case(
        "github:alice",
        workspace_id=workspace.workspace_id,
        title="案件",
        assessment_date=date(2026, 8, 6),
    )
    workspace_uc.add_or_update_member(
        workspace.workspace_id,
        "github:alice",
        user_id="github:editor",
        role="editor",
    )
    return (
        assessment_uc,
        policy_uc,
        fact_repo,
        case_repo,
        assessment_repo,
        workspace.workspace_id,
        case.case_id,
    )


def _confirmed_fact(case_id: str, field_name: str, value: object) -> CaseFact:
    return CaseFact(
        fact_id=f"fact_{field_name}",
        case_id=case_id,
        field_name=field_name,
        value=value,  # type: ignore[arg-type]
        status="confirmed",
        source_type="user",
        confidence=1.0,
        created_by="github:editor",
        confirmed_by="github:alice",
        confirmed_at=101.0,
        created_at=100.0,
        updated_at=101.0,
    )


def _rule(
    workspace_id: str,
    *,
    required_fact_fields: list[str],
    condition: dict,
    result: dict,
) -> PolicyRule:
    return PolicyRule(
        workspace_id=workspace_id,
        rule_id="SYNTHETIC-001",
        ruleset_version="synthetic-v1",
        jurisdiction="CN",
        effective_from=date(2026, 1, 1),
        status="published",
        required_fact_fields=required_fact_fields,
        condition=condition,
        result=result,
        source_clause_ids=["synthetic-clause"],
    )


def _publish_rule(
    policy_uc: PolicyManagementUseCase,
    workspace_id: str,
    rule: PolicyRule,
) -> None:
    created = policy_uc.create_rule(
        workspace_id,
        "github:alice",
        rule.model_copy(update={"status": "draft"}),
    )
    policy_uc.publish_rule(
        workspace_id,
        "github:alice",
        rule_id=created.rule_id,
        ruleset_version=created.ruleset_version,
    )


class TestAssessmentGeneration:
    def test_triggered_rule_generates_finding_action_and_snapshot(self) -> None:
        (
            assessment_uc,
            policy_uc,
            fact_repo,
            _,
            _,
            workspace_id,
            case_id,
        ) = _setup()
        fact = _confirmed_fact(case_id, "flag", True)
        fact_repo.create(fact, [])
        _publish_rule(
            policy_uc,
            workspace_id,
            _rule(
                workspace_id,
                required_fact_fields=["flag"],
                condition={"field": "flag", "operator": "eq", "value": True},
                result={
                    "candidate_path": "synthetic",
                    "risk_level": "high",
                    "required_actions": ["执行合成检查"],
                    "required_materials": ["合成材料"],
                },
            ),
        )
        bundle = assessment_uc.generate(
            case_id,
            "github:editor",
            ruleset_version="synthetic-v1",
            generated_by_run_id="run_001",
        )
        assert bundle.assessment.status == "review_required"
        assert bundle.assessment.fact_versions == {"flag": 1}
        assert bundle.assessment.risk_level == "high"
        assert bundle.assessment.candidate_paths == ["synthetic"]
        assert bundle.assessment.generated_by_run_id == "run_001"
        assert bundle.findings[0].finding_type == "rule_trigger"
        assert bundle.findings[0].fact_ids == [fact.fact_id]
        assert {item.title for item in bundle.action_items} == {
            "执行合成检查",
            "补充材料：合成材料",
        }

    def test_missing_fact_generates_gap_finding_and_action(self) -> None:
        (
            assessment_uc,
            policy_uc,
            _,
            _,
            _,
            workspace_id,
            case_id,
        ) = _setup()
        _publish_rule(
            policy_uc,
            workspace_id,
            _rule(
                workspace_id,
                required_fact_fields=["missing_field"],
                condition={
                    "field": "missing_field",
                    "operator": "eq",
                    "value": True,
                },
                result={"candidate_path": "synthetic"},
            ),
        )
        bundle = assessment_uc.generate(
            case_id,
            "github:editor",
            ruleset_version="synthetic-v1",
        )
        assert bundle.assessment.risk_level == "unknown"
        assert bundle.findings[0].finding_type == "missing_fact"
        assert bundle.action_items[0].title == "确认事实：missing_field"

    def test_second_version_supersedes_first_and_updates_active_case(self) -> None:
        (
            assessment_uc,
            policy_uc,
            fact_repo,
            case_repo,
            assessment_repo,
            workspace_id,
            case_id,
        ) = _setup()
        fact_repo.create(_confirmed_fact(case_id, "flag", True), [])
        _publish_rule(
            policy_uc,
            workspace_id,
            _rule(
                workspace_id,
                required_fact_fields=["flag"],
                condition={"field": "flag", "operator": "eq", "value": True},
                result={"risk_level": "medium"},
            ),
        )
        first = assessment_uc.generate(
            case_id,
            "github:editor",
            ruleset_version="synthetic-v1",
        )
        second = assessment_uc.generate(
            case_id,
            "github:editor",
            ruleset_version="synthetic-v1",
        )
        assert first.assessment.version == 1
        assert second.assessment.version == 2
        versions = assessment_repo.list_for_case(case_id)
        assert versions[1].status == "superseded"
        active_case = case_repo.get(case_id)
        assert active_case is not None
        assert active_case.active_assessment_id == second.assessment.assessment_id

    def test_duplicate_confirmed_field_rejected(self) -> None:
        (
            assessment_uc,
            policy_uc,
            fact_repo,
            _,
            _,
            workspace_id,
            case_id,
        ) = _setup()
        first = _confirmed_fact(case_id, "flag", True)
        second = first.model_copy(update={"fact_id": "fact_flag_2"})
        fact_repo.create(first, [])
        fact_repo.create(second, [])
        _publish_rule(
            policy_uc,
            workspace_id,
            _rule(
                workspace_id,
                required_fact_fields=["flag"],
                condition={"field": "flag", "operator": "eq", "value": True},
                result={"risk_level": "high"},
            ),
        )
        with pytest.raises(ValueError, match="多个 confirmed"):
            assessment_uc.generate(
                case_id,
                "github:editor",
                ruleset_version="synthetic-v1",
            )

    def test_invalid_rule_result_rejected(self) -> None:
        (
            assessment_uc,
            policy_uc,
            fact_repo,
            _,
            _,
            workspace_id,
            case_id,
        ) = _setup()
        fact_repo.create(_confirmed_fact(case_id, "flag", True), [])
        _publish_rule(
            policy_uc,
            workspace_id,
            _rule(
                workspace_id,
                required_fact_fields=["flag"],
                condition={"field": "flag", "operator": "eq", "value": True},
                result={"risk_level": "impossible"},
            ),
        )
        with pytest.raises(ValueError, match="risk_level"):
            assessment_uc.generate(
                case_id,
                "github:editor",
                ruleset_version="synthetic-v1",
            )
