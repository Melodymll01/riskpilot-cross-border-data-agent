"""SQLAlchemy WorkspaceRepoPort 实现。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from domain.workspaces import Workspace, WorkspaceMembership
from infra.storage.sqlalchemy.database import SqlAlchemyDatabase
from infra.storage.sqlalchemy.mapping import (
    require_datetime,
    require_timestamp,
    to_datetime,
)
from infra.storage.sqlalchemy.models import WorkspaceMembershipRow, WorkspaceRow


class SqlAlchemyWorkspaceRepo:
    def __init__(self, database: SqlAlchemyDatabase) -> None:
        self._database = database

    def create(
        self,
        workspace: Workspace,
        creator_membership: WorkspaceMembership,
    ) -> None:
        if creator_membership.workspace_id != workspace.workspace_id:
            raise ValueError("创建者成员关系必须属于新建 Workspace")
        if creator_membership.user_id != workspace.created_by:
            raise ValueError("创建者成员关系必须属于 Workspace 创建者")
        if creator_membership.role != "admin":
            raise ValueError("Workspace 创建者必须具有 admin 角色")
        with self._database.session() as session:
            session.add(_workspace_row(workspace))
            session.flush()
            session.add(_membership_row(creator_membership))

    def get(self, workspace_id: str) -> Workspace | None:
        with self._database.read_session() as session:
            row = session.get(WorkspaceRow, workspace_id)
            return None if row is None else _workspace(row)

    def list_for_user(self, user_id: str, limit: int = 50) -> list[Workspace]:
        statement = (
            select(WorkspaceRow)
            .join(
                WorkspaceMembershipRow,
                WorkspaceMembershipRow.workspace_id == WorkspaceRow.workspace_id,
            )
            .where(WorkspaceMembershipRow.user_id == user_id)
            .order_by(WorkspaceRow.updated_at.desc())
            .limit(limit)
        )
        with self._database.read_session() as session:
            return [_workspace(row) for row in session.scalars(statement)]

    def get_membership(
        self,
        workspace_id: str,
        user_id: str,
    ) -> WorkspaceMembership | None:
        with self._database.read_session() as session:
            row = session.get(WorkspaceMembershipRow, (workspace_id, user_id))
            return None if row is None else _membership(row)

    def upsert_membership(self, membership: WorkspaceMembership) -> None:
        values = {
            "workspace_id": membership.workspace_id,
            "user_id": membership.user_id,
            "role": membership.role,
            "joined_at": to_datetime(membership.joined_at),
        }
        with self._database.session() as session:
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                statement = pg_insert(WorkspaceMembershipRow).values(**values)
                session.execute(
                    statement.on_conflict_do_update(
                        index_elements=["workspace_id", "user_id"],
                        set_={"role": statement.excluded.role},
                    )
                )
            else:
                row = session.get(
                    WorkspaceMembershipRow,
                    (membership.workspace_id, membership.user_id),
                )
                if row is None:
                    session.add(WorkspaceMembershipRow(**values))
                else:
                    row.role = membership.role

    def list_memberships(self, workspace_id: str) -> list[WorkspaceMembership]:
        statement = (
            select(WorkspaceMembershipRow)
            .where(WorkspaceMembershipRow.workspace_id == workspace_id)
            .order_by(
                WorkspaceMembershipRow.joined_at,
                WorkspaceMembershipRow.user_id,
            )
        )
        with self._database.read_session() as session:
            return [_membership(row) for row in session.scalars(statement)]


def _workspace_row(workspace: Workspace) -> WorkspaceRow:
    return WorkspaceRow(
        workspace_id=workspace.workspace_id,
        name=workspace.name,
        status=workspace.status,
        created_by=workspace.created_by,
        created_at=require_datetime(workspace.created_at),
        updated_at=require_datetime(workspace.updated_at),
    )


def _membership_row(membership: WorkspaceMembership) -> WorkspaceMembershipRow:
    return WorkspaceMembershipRow(
        workspace_id=membership.workspace_id,
        user_id=membership.user_id,
        role=membership.role,
        joined_at=require_datetime(membership.joined_at),
    )


def _workspace(row: WorkspaceRow) -> Workspace:
    return Workspace(
        workspace_id=row.workspace_id,
        name=row.name,
        status=row.status,
        created_by=row.created_by,
        created_at=require_timestamp(row.created_at),
        updated_at=require_timestamp(row.updated_at),
    )


def _membership(row: WorkspaceMembershipRow) -> WorkspaceMembership:
    return WorkspaceMembership(
        workspace_id=row.workspace_id,
        user_id=row.user_id,
        role=row.role,
        joined_at=require_timestamp(row.joined_at),
    )
