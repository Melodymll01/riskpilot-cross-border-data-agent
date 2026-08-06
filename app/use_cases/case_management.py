"""V2 合规案件应用用例。"""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping
from datetime import date
from typing import TYPE_CHECKING, Any, cast

from domain.cases import Case, CaseStatus
from domain.errors import (
    CaseArchived,
    CaseNotFound,
    WorkspaceAccessDenied,
    WorkspaceNotFound,
)

if TYPE_CHECKING:
    from domain.ports import CaseRepoPort, WorkspaceRepoPort
    from domain.workspaces import WorkspaceMembership, WorkspaceRole

_CASE_WRITE_ROLES: set[WorkspaceRole] = {"editor", "reviewer", "admin"}
_CASE_REVIEW_ROLES: set[WorkspaceRole] = {"reviewer", "admin"}
_UPDATABLE_FIELDS = {
    "title",
    "description",
    "jurisdiction",
    "scenario_type",
    "assessment_date",
    "reviewer_id",
}


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class CaseManagementUseCase:
    """案件创建、查询、资料更新与状态转换。"""

    def __init__(
        self,
        *,
        case_repo: CaseRepoPort,
        workspace_repo: WorkspaceRepoPort,
    ) -> None:
        self._case_repo = case_repo
        self._workspace_repo = workspace_repo

    def create_case(
        self,
        actor_id: str,
        *,
        workspace_id: str,
        title: str,
        description: str = "",
        jurisdiction: str = "CN",
        scenario_type: str = "",
        assessment_date: date | None = None,
        reviewer_id: str | None = None,
    ) -> Case:
        self._require_role(
            workspace_id,
            actor_id,
            _CASE_WRITE_ROLES,
            action="创建案件",
        )
        if reviewer_id is not None:
            self._require_reviewer(workspace_id, reviewer_id)
        now = time.time()
        case = Case(
            case_id=_new_id("case"),
            workspace_id=workspace_id,
            title=title,
            description=description,
            jurisdiction=jurisdiction,
            scenario_type=scenario_type,
            assessment_date=assessment_date,
            owner_id=actor_id,
            reviewer_id=reviewer_id,
            created_at=now,
            updated_at=now,
        )
        self._case_repo.create(case)
        return case

    def list_cases(
        self,
        actor_id: str,
        *,
        workspace_id: str,
        include_archived: bool = False,
        limit: int = 50,
    ) -> list[Case]:
        self._require_membership(workspace_id, actor_id)
        return self._case_repo.list_for_workspace(
            workspace_id,
            include_archived=include_archived,
            limit=limit,
        )

    def get_case(self, case_id: str, actor_id: str) -> Case:
        case = self._case_repo.get(case_id)
        if case is None:
            raise CaseNotFound(case_id)
        if self._workspace_repo.get_membership(case.workspace_id, actor_id) is None:
            raise CaseNotFound(case_id)
        return case

    def update_case(
        self,
        case_id: str,
        actor_id: str,
        *,
        changes: Mapping[str, Any],
    ) -> Case:
        case = self.get_case(case_id, actor_id)
        self._ensure_mutable(case)
        self._require_role(
            case.workspace_id,
            actor_id,
            _CASE_WRITE_ROLES,
            action="修改案件",
        )
        unknown_fields = set(changes) - _UPDATABLE_FIELDS
        if unknown_fields:
            fields = ", ".join(sorted(unknown_fields))
            raise ValueError(f"不允许修改字段: {fields}")
        if "reviewer_id" in changes and changes["reviewer_id"] is not None:
            self._require_reviewer(case.workspace_id, str(changes["reviewer_id"]))
        if not changes:
            return case
        payload = case.model_dump()
        payload.update(changes)
        payload["updated_at"] = time.time()
        updated = cast("Case", Case.model_validate(payload))
        self._case_repo.update(updated)
        return updated

    def transition_case(
        self,
        case_id: str,
        actor_id: str,
        target: CaseStatus,
    ) -> Case:
        case = self.get_case(case_id, actor_id)
        if target == case.status:
            return case
        self._ensure_mutable(case)
        allowed_roles = _CASE_REVIEW_ROLES if target == "completed" else _CASE_WRITE_ROLES
        self._require_role(
            case.workspace_id,
            actor_id,
            allowed_roles,
            action=f"将案件状态更新为 {target}",
        )
        updated = case.transition_to(target)
        if updated is not case:
            self._case_repo.update(updated)
        return updated

    def _require_membership(self, workspace_id: str, actor_id: str) -> WorkspaceMembership:
        membership = self._workspace_repo.get_membership(workspace_id, actor_id)
        if membership is None:
            raise WorkspaceNotFound(workspace_id)
        return membership

    def _require_role(
        self,
        workspace_id: str,
        actor_id: str,
        allowed_roles: set[WorkspaceRole],
        *,
        action: str,
    ) -> WorkspaceMembership:
        membership = self._require_membership(workspace_id, actor_id)
        if membership.role not in allowed_roles:
            raise WorkspaceAccessDenied(workspace_id, actor_id, action)
        return membership

    def _require_reviewer(self, workspace_id: str, reviewer_id: str) -> None:
        membership = self._workspace_repo.get_membership(workspace_id, reviewer_id)
        if membership is None or membership.role not in _CASE_REVIEW_ROLES:
            raise ValueError("reviewer_id 必须是当前 Workspace 的 reviewer 或 admin")

    @staticmethod
    def _ensure_mutable(case: Case) -> None:
        if case.status == "archived":
            raise CaseArchived(case.case_id)
