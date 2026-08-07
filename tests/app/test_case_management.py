"""V2 CaseManagementUseCase 测试。"""

from __future__ import annotations

import pytest

from app.use_cases import CaseManagementUseCase, WorkspaceManagementUseCase
from domain import (
    CaseArchived,
    CaseNotFound,
    InvalidCaseTransition,
    WorkspaceAccessDenied,
)
from tests.fakes import InMemoryCaseRepo, InMemoryWorkspaceRepo


def _setup() -> tuple[
    WorkspaceManagementUseCase,
    CaseManagementUseCase,
    str,
]:
    workspace_repo = InMemoryWorkspaceRepo()
    case_repo = InMemoryCaseRepo()
    workspace_uc = WorkspaceManagementUseCase(workspace_repo)
    case_uc = CaseManagementUseCase(
        case_repo=case_repo,
        workspace_repo=workspace_repo,
    )
    workspace = workspace_uc.create_workspace("github:alice", name="跨境合规组")
    return workspace_uc, case_uc, workspace.workspace_id


class TestCaseAccess:
    def test_editor_can_create_and_list_case(self) -> None:
        workspace_uc, case_uc, workspace_id = _setup()
        workspace_uc.add_or_update_member(
            workspace_id,
            "github:alice",
            user_id="github:bob",
            role="editor",
        )
        case = case_uc.create_case(
            "github:bob",
            workspace_id=workspace_id,
            title="海外客服项目",
        )
        assert case.owner_id == "github:bob"
        assert case_uc.list_cases("github:bob", workspace_id=workspace_id) == [case]

    def test_viewer_cannot_create_case(self) -> None:
        workspace_uc, case_uc, workspace_id = _setup()
        workspace_uc.add_or_update_member(
            workspace_id,
            "github:alice",
            user_id="github:bob",
            role="viewer",
        )
        with pytest.raises(WorkspaceAccessDenied):
            case_uc.create_case(
                "github:bob",
                workspace_id=workspace_id,
                title="不允许创建",
            )

    def test_non_member_cannot_discover_case(self) -> None:
        _, case_uc, workspace_id = _setup()
        case = case_uc.create_case(
            "github:alice",
            workspace_id=workspace_id,
            title="私有案件",
        )
        with pytest.raises(CaseNotFound):
            case_uc.get_case(case.case_id, "github:outsider")


class TestCaseUpdates:
    def test_editor_can_update_metadata(self) -> None:
        workspace_uc, case_uc, workspace_id = _setup()
        workspace_uc.add_or_update_member(
            workspace_id,
            "github:alice",
            user_id="github:bob",
            role="editor",
        )
        case = case_uc.create_case(
            "github:bob",
            workspace_id=workspace_id,
            title="旧标题",
        )
        updated = case_uc.update_case(
            case.case_id,
            "github:bob",
            changes={"title": "新标题", "scenario_type": "personal_information"},
        )
        assert updated.title == "新标题"
        assert updated.scenario_type == "personal_information"

    def test_unknown_field_rejected(self) -> None:
        _, case_uc, workspace_id = _setup()
        case = case_uc.create_case(
            "github:alice",
            workspace_id=workspace_id,
            title="案件",
        )
        with pytest.raises(ValueError, match="status"):
            case_uc.update_case(
                case.case_id,
                "github:alice",
                changes={"status": "completed"},
            )

    def test_reviewer_must_have_workspace_role(self) -> None:
        _, case_uc, workspace_id = _setup()
        with pytest.raises(ValueError, match="reviewer"):
            case_uc.create_case(
                "github:alice",
                workspace_id=workspace_id,
                title="案件",
                reviewer_id="github:outsider",
            )


class TestCaseTransitions:
    def test_completed_requires_assessment_approval(self) -> None:
        workspace_uc, case_uc, workspace_id = _setup()
        workspace_uc.add_or_update_member(
            workspace_id,
            "github:alice",
            user_id="github:editor",
            role="editor",
        )
        workspace_uc.add_or_update_member(
            workspace_id,
            "github:alice",
            user_id="github:reviewer",
            role="reviewer",
        )
        case = case_uc.create_case(
            "github:editor",
            workspace_id=workspace_id,
            title="案件",
            reviewer_id="github:reviewer",
        )
        for target in (
            "collecting",
            "ready_for_assessment",
            "assessing",
            "review_required",
        ):
            case = case_uc.transition_case(
                case.case_id,
                "github:editor",
                target,  # type: ignore[arg-type]
            )
        with pytest.raises(ValueError, match="Assessment"):
            case_uc.transition_case(case.case_id, "github:editor", "completed")
        with pytest.raises(ValueError, match="Assessment"):
            case_uc.transition_case(case.case_id, "github:reviewer", "completed")

    def test_invalid_transition_uses_domain_state_machine(self) -> None:
        _, case_uc, workspace_id = _setup()
        case = case_uc.create_case(
            "github:alice",
            workspace_id=workspace_id,
            title="案件",
        )
        with pytest.raises(InvalidCaseTransition):
            case_uc.transition_case(case.case_id, "github:alice", "completed")

    def test_review_required_cannot_bypass_assessment_review(self) -> None:
        _, case_uc, workspace_id = _setup()
        case = case_uc.create_case(
            "github:alice",
            workspace_id=workspace_id,
            title="案件",
        )
        for target in (
            "collecting",
            "ready_for_assessment",
            "assessing",
            "review_required",
        ):
            case = case_uc.transition_case(case.case_id, "github:alice", target)

        with pytest.raises(ValueError, match="Assessment"):
            case_uc.transition_case(
                case.case_id,
                "github:alice",
                "ready_for_assessment",
            )

    def test_archived_case_cannot_be_updated(self) -> None:
        _, case_uc, workspace_id = _setup()
        case = case_uc.create_case(
            "github:alice",
            workspace_id=workspace_id,
            title="案件",
        )
        case_uc.transition_case(case.case_id, "github:alice", "archived")
        with pytest.raises(CaseArchived):
            case_uc.update_case(
                case.case_id,
                "github:alice",
                changes={"title": "归档后修改"},
            )

    def test_repeated_archive_transition_is_idempotent(self) -> None:
        _, case_uc, workspace_id = _setup()
        case = case_uc.create_case(
            "github:alice",
            workspace_id=workspace_id,
            title="案件",
        )
        archived = case_uc.transition_case(case.case_id, "github:alice", "archived")
        assert case_uc.transition_case(archived.case_id, "github:alice", "archived") == archived
