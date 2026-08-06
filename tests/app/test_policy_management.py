"""PolicyManagementUseCase 测试。"""

from __future__ import annotations

from datetime import date

import pytest

from app.use_cases import (
    CaseManagementUseCase,
    PolicyManagementUseCase,
    WorkspaceManagementUseCase,
)
from domain import CaseFact, PolicyRule, WorkspaceAccessDenied
from tests.fakes import (
    InMemoryCaseFactRepo,
    InMemoryCaseRepo,
    InMemoryPolicyRuleRepo,
    InMemoryWorkspaceRepo,
)


def _setup(*, assessment_date: date | None = date(2026, 8, 6)):
    workspace_repo = InMemoryWorkspaceRepo()
    case_repo = InMemoryCaseRepo()
    fact_repo = InMemoryCaseFactRepo()
    rule_repo = InMemoryPolicyRuleRepo()
    workspace_uc = WorkspaceManagementUseCase(workspace_repo)
    case_uc = CaseManagementUseCase(
        case_repo=case_repo,
        workspace_repo=workspace_repo,
    )
    use_case = PolicyManagementUseCase(
        rule_repo=rule_repo,
        fact_repo=fact_repo,
        case_management=case_uc,
        workspace_management=workspace_uc,
    )
    workspace = workspace_uc.create_workspace("github:alice", name="跨境合规组")
    case = case_uc.create_case(
        "github:alice",
        workspace_id=workspace.workspace_id,
        title="案件",
        assessment_date=assessment_date,
    )
    workspace_uc.add_or_update_member(
        workspace.workspace_id,
        "github:alice",
        user_id="github:editor",
        role="editor",
    )
    return use_case, fact_repo, workspace.workspace_id, case.case_id


def _rule(workspace_id: str = "ws_placeholder") -> PolicyRule:
    return PolicyRule(
        workspace_id=workspace_id,
        rule_id="SYNTHETIC-001",
        ruleset_version="synthetic-v1",
        jurisdiction="CN",
        effective_from=date(2026, 1, 1),
        status="draft",
        required_fact_fields=["flag"],
        condition={"field": "flag", "operator": "eq", "value": True},
        result={"candidate_path": "synthetic"},
        source_clause_ids=["synthetic-clause"],
    )


def _fact(status: str = "confirmed") -> CaseFact:
    values: dict[str, object] = {
        "fact_id": "fact_flag",
        "case_id": "case_placeholder",
        "field_name": "flag",
        "value": True,
        "status": status,
        "source_type": "user",
        "confidence": 1.0,
        "created_by": "github:editor",
        "created_at": 100.0,
        "updated_at": 101.0,
    }
    if status == "confirmed":
        values.update(
            {
                "confirmed_by": "github:alice",
                "confirmed_at": 101.0,
            }
        )
    return CaseFact(**values)  # type: ignore[arg-type]


class TestRuleManagement:
    def test_admin_create_publish_and_list(self) -> None:
        use_case, _, workspace_id, _ = _setup()
        created = use_case.create_rule(workspace_id, "github:alice", _rule(workspace_id))
        assert created.status == "draft"
        published = use_case.publish_rule(
            workspace_id,
            "github:alice",
            rule_id=created.rule_id,
            ruleset_version=created.ruleset_version,
        )
        assert published.status == "published"
        assert use_case.list_rules(
            workspace_id,
            "github:editor",
            status="published",
        ) == [published]

    def test_editor_cannot_create_rule(self) -> None:
        use_case, _, workspace_id, _ = _setup()
        with pytest.raises(WorkspaceAccessDenied):
            use_case.create_rule(workspace_id, "github:editor", _rule(workspace_id))

    def test_invalid_condition_rejected_before_persist(self) -> None:
        use_case, _, workspace_id, _ = _setup()
        invalid = _rule(workspace_id).model_copy(
            update={
                "condition": {
                    "field": "undeclared",
                    "operator": "eq",
                    "value": True,
                }
            }
        )
        with pytest.raises(ValueError, match="undeclared"):
            use_case.create_rule(workspace_id, "github:alice", invalid)


class TestCaseEvaluation:
    def test_confirmed_fact_triggers_published_rule(self) -> None:
        use_case, fact_repo, workspace_id, case_id = _setup()
        fact = _fact().model_copy(update={"case_id": case_id})
        fact_repo.create(fact, [])
        created = use_case.create_rule(workspace_id, "github:alice", _rule(workspace_id))
        use_case.publish_rule(
            workspace_id,
            "github:alice",
            rule_id=created.rule_id,
            ruleset_version=created.ruleset_version,
        )
        report = use_case.evaluate_case(
            case_id,
            "github:editor",
            ruleset_version="synthetic-v1",
        )
        assert report.triggered[0].result["candidate_path"] == "synthetic"

    def test_proposed_fact_is_reported_missing(self) -> None:
        use_case, fact_repo, workspace_id, case_id = _setup()
        fact = _fact(status="proposed").model_copy(update={"case_id": case_id})
        fact_repo.create(fact, [])
        created = use_case.create_rule(workspace_id, "github:alice", _rule(workspace_id))
        use_case.publish_rule(
            workspace_id,
            "github:alice",
            rule_id=created.rule_id,
            ruleset_version=created.ruleset_version,
        )
        report = use_case.evaluate_case(
            case_id,
            "github:editor",
            ruleset_version="synthetic-v1",
        )
        assert report.missing_fact_fields == ["flag"]

    def test_assessment_date_is_required(self) -> None:
        use_case, _, _, case_id = _setup(assessment_date=None)
        with pytest.raises(ValueError, match="assessment_date"):
            use_case.evaluate_case(
                case_id,
                "github:alice",
                ruleset_version="synthetic-v1",
            )
