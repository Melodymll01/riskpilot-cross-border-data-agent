"""SQLite WorkspaceRepoPort 实现。"""

from __future__ import annotations

from typing import Any

from domain.workspaces import (
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
    WorkspaceStatus,
)
from infra.storage._db import SqliteConnectionPool


class SqliteWorkspaceRepo:
    """Workspace 与成员关系共用同一事务边界。"""

    def __init__(self, pool: SqliteConnectionPool) -> None:
        self._pool = pool

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
        conn = self._pool.get()
        with conn:
            conn.execute(
                """
                INSERT INTO workspaces
                    (workspace_id, name, status, created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    workspace.workspace_id,
                    workspace.name,
                    workspace.status,
                    workspace.created_by,
                    workspace.created_at,
                    workspace.updated_at,
                ),
            )
            conn.execute(
                """
                INSERT INTO workspace_memberships
                    (workspace_id, user_id, role, joined_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    creator_membership.workspace_id,
                    creator_membership.user_id,
                    creator_membership.role,
                    creator_membership.joined_at,
                ),
            )

    def get(self, workspace_id: str) -> Workspace | None:
        row = (
            self._pool.get()
            .execute(
                "SELECT * FROM workspaces WHERE workspace_id = ?",
                (workspace_id,),
            )
            .fetchone()
        )
        return None if row is None else _row_to_workspace(row)

    def list_for_user(self, user_id: str, limit: int = 50) -> list[Workspace]:
        rows = (
            self._pool.get()
            .execute(
                """
                SELECT w.*
                FROM workspaces AS w
                JOIN workspace_memberships AS m
                  ON m.workspace_id = w.workspace_id
                WHERE m.user_id = ?
                ORDER BY w.updated_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            )
            .fetchall()
        )
        return [_row_to_workspace(row) for row in rows]

    def get_membership(self, workspace_id: str, user_id: str) -> WorkspaceMembership | None:
        row = (
            self._pool.get()
            .execute(
                """
                SELECT * FROM workspace_memberships
                WHERE workspace_id = ? AND user_id = ?
                """,
                (workspace_id, user_id),
            )
            .fetchone()
        )
        return None if row is None else _row_to_membership(row)

    def upsert_membership(self, membership: WorkspaceMembership) -> None:
        conn = self._pool.get()
        conn.execute(
            """
            INSERT INTO workspace_memberships
                (workspace_id, user_id, role, joined_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(workspace_id, user_id) DO UPDATE SET
                role = excluded.role
            """,
            (
                membership.workspace_id,
                membership.user_id,
                membership.role,
                membership.joined_at,
            ),
        )
        conn.commit()

    def list_memberships(self, workspace_id: str) -> list[WorkspaceMembership]:
        rows = (
            self._pool.get()
            .execute(
                """
                SELECT * FROM workspace_memberships
                WHERE workspace_id = ?
                ORDER BY joined_at, user_id
                """,
                (workspace_id,),
            )
            .fetchall()
        )
        return [_row_to_membership(row) for row in rows]


def _row_to_workspace(row: Any) -> Workspace:
    return Workspace(
        workspace_id=row["workspace_id"],
        name=row["name"],
        status=_validate_workspace_status(row["status"]),
        created_by=row["created_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_membership(row: Any) -> WorkspaceMembership:
    return WorkspaceMembership(
        workspace_id=row["workspace_id"],
        user_id=row["user_id"],
        role=_validate_workspace_role(row["role"]),
        joined_at=row["joined_at"],
    )


def _validate_workspace_status(value: str) -> WorkspaceStatus:
    if value not in ("active", "archived"):
        raise ValueError(f"invalid workspace status in DB: {value!r}")
    return value  # type: ignore[return-value]


def _validate_workspace_role(value: str) -> WorkspaceRole:
    if value not in ("viewer", "editor", "reviewer", "admin"):
        raise ValueError(f"invalid workspace role in DB: {value!r}")
    return value  # type: ignore[return-value]
