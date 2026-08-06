"""V3 Workspace/Case SQLite Repository 集成测试。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from domain import (
    Case,
    CaseRepoPort,
    Workspace,
    WorkspaceMembership,
    WorkspaceRepoPort,
)
from infra.storage import SqliteCaseRepo, SqliteWorkspaceRepo
from infra.storage._db import SqliteConnectionPool


@pytest.fixture
def pool(tmp_path: Path) -> SqliteConnectionPool:
    return SqliteConnectionPool(str(tmp_path / "v3.db"))


@pytest.fixture
def workspace_repo(pool: SqliteConnectionPool) -> SqliteWorkspaceRepo:
    return SqliteWorkspaceRepo(pool)


@pytest.fixture
def case_repo(pool: SqliteConnectionPool) -> SqliteCaseRepo:
    return SqliteCaseRepo(pool)


def _workspace(workspace_id: str = "ws_001", updated_at: float = 100.0) -> Workspace:
    return Workspace(
        workspace_id=workspace_id,
        name=f"工作空间 {workspace_id}",
        created_by="github:alice",
        created_at=100.0,
        updated_at=updated_at,
    )


def _membership(
    workspace_id: str = "ws_001",
    user_id: str = "github:alice",
    role: str = "admin",
) -> WorkspaceMembership:
    return WorkspaceMembership(
        workspace_id=workspace_id,
        user_id=user_id,
        role=role,  # type: ignore[arg-type]
        joined_at=100.0,
    )


def _case(
    case_id: str = "case_001",
    workspace_id: str = "ws_001",
    *,
    status: str = "draft",
    updated_at: float = 100.0,
) -> Case:
    return Case(
        case_id=case_id,
        workspace_id=workspace_id,
        title=f"案件 {case_id}",
        description="测试案件",
        jurisdiction="CN",
        scenario_type="personal_information",
        assessment_date=date(2026, 8, 6),
        status=status,  # type: ignore[arg-type]
        owner_id="github:alice",
        created_at=100.0,
        updated_at=updated_at,
    )


class TestProtocolConformance:
    def test_workspace_repo(self, workspace_repo: SqliteWorkspaceRepo) -> None:
        assert isinstance(workspace_repo, WorkspaceRepoPort)

    def test_case_repo(self, case_repo: SqliteCaseRepo) -> None:
        assert isinstance(case_repo, CaseRepoPort)


class TestWorkspaceRepo:
    def test_create_is_atomic_with_creator_membership(
        self, workspace_repo: SqliteWorkspaceRepo
    ) -> None:
        workspace_repo.create(_workspace(), _membership())
        assert workspace_repo.get("ws_001") is not None
        membership = workspace_repo.get_membership("ws_001", "github:alice")
        assert membership is not None
        assert membership.role == "admin"

    def test_invalid_creator_membership_does_not_write_workspace(
        self, workspace_repo: SqliteWorkspaceRepo
    ) -> None:
        with pytest.raises(ValueError, match="admin"):
            workspace_repo.create(_workspace(), _membership(role="editor"))
        assert workspace_repo.get("ws_001") is None

    def test_list_for_user_only_returns_joined_workspaces(
        self, workspace_repo: SqliteWorkspaceRepo
    ) -> None:
        workspace_repo.create(_workspace("ws_old", 100.0), _membership("ws_old"))
        workspace_repo.create(_workspace("ws_new", 200.0), _membership("ws_new"))
        assert [w.workspace_id for w in workspace_repo.list_for_user("github:alice")] == [
            "ws_new",
            "ws_old",
        ]
        assert workspace_repo.list_for_user("github:bob") == []

    def test_upsert_membership_preserves_joined_at(
        self, workspace_repo: SqliteWorkspaceRepo
    ) -> None:
        workspace_repo.create(_workspace(), _membership())
        workspace_repo.upsert_membership(_membership(user_id="github:bob", role="viewer"))
        workspace_repo.upsert_membership(
            WorkspaceMembership(
                workspace_id="ws_001",
                user_id="github:bob",
                role="reviewer",
                joined_at=999.0,
            )
        )
        loaded = workspace_repo.get_membership("ws_001", "github:bob")
        assert loaded is not None
        assert loaded.role == "reviewer"
        assert loaded.joined_at == 100.0


class TestCaseRepo:
    def test_create_get_and_date_round_trip(
        self,
        workspace_repo: SqliteWorkspaceRepo,
        case_repo: SqliteCaseRepo,
    ) -> None:
        workspace_repo.create(_workspace(), _membership())
        case_repo.create(_case())
        loaded = case_repo.get("case_001")
        assert loaded is not None
        assert loaded.assessment_date == date(2026, 8, 6)
        assert loaded.workspace_id == "ws_001"

    def test_list_filters_workspace_and_archived(
        self,
        workspace_repo: SqliteWorkspaceRepo,
        case_repo: SqliteCaseRepo,
    ) -> None:
        workspace_repo.create(_workspace("ws_001"), _membership("ws_001"))
        workspace_repo.create(_workspace("ws_002"), _membership("ws_002"))
        case_repo.create(_case("case_open", "ws_001", updated_at=200.0))
        case_repo.create(_case("case_archived", "ws_001", status="archived", updated_at=300.0))
        case_repo.create(_case("case_other", "ws_002"))

        assert [c.case_id for c in case_repo.list_for_workspace("ws_001")] == ["case_open"]
        assert [
            c.case_id for c in case_repo.list_for_workspace("ws_001", include_archived=True)
        ] == ["case_archived", "case_open"]

    def test_update_persists_transition(
        self,
        workspace_repo: SqliteWorkspaceRepo,
        case_repo: SqliteCaseRepo,
    ) -> None:
        workspace_repo.create(_workspace(), _membership())
        case = _case()
        case_repo.create(case)
        case_repo.update(case.transition_to("collecting", at=101.0))
        loaded = case_repo.get(case.case_id)
        assert loaded is not None
        assert loaded.status == "collecting"
        assert loaded.updated_at == 101.0
