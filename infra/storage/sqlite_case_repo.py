"""SQLite CaseRepoPort 实现。"""

from __future__ import annotations

from datetime import date
from typing import Any

from domain.cases import Case, CaseStatus
from infra.storage._db import SqliteConnectionPool


class SqliteCaseRepo:
    def __init__(self, pool: SqliteConnectionPool) -> None:
        self._pool = pool

    def create(self, case: Case) -> None:
        conn = self._pool.get()
        conn.execute(
            """
            INSERT INTO compliance_cases
                (case_id, workspace_id, title, description, jurisdiction,
                 scenario_type, assessment_date, status, owner_id, reviewer_id,
                 active_assessment_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _case_values(case),
        )
        conn.commit()

    def get(self, case_id: str) -> Case | None:
        row = (
            self._pool.get()
            .execute(
                "SELECT * FROM compliance_cases WHERE case_id = ?",
                (case_id,),
            )
            .fetchone()
        )
        return None if row is None else _row_to_case(row)

    def list_for_workspace(
        self,
        workspace_id: str,
        *,
        include_archived: bool = False,
        limit: int = 50,
    ) -> list[Case]:
        conn = self._pool.get()
        if include_archived:
            rows = conn.execute(
                """
                SELECT * FROM compliance_cases
                WHERE workspace_id = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (workspace_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM compliance_cases
                WHERE workspace_id = ? AND status != 'archived'
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (workspace_id, limit),
            ).fetchall()
        return [_row_to_case(row) for row in rows]

    def update(self, case: Case) -> None:
        conn = self._pool.get()
        conn.execute(
            """
            UPDATE compliance_cases SET
                workspace_id = ?,
                title = ?,
                description = ?,
                jurisdiction = ?,
                scenario_type = ?,
                assessment_date = ?,
                status = ?,
                owner_id = ?,
                reviewer_id = ?,
                active_assessment_id = ?,
                created_at = ?,
                updated_at = ?
            WHERE case_id = ?
            """,
            (
                case.workspace_id,
                case.title,
                case.description,
                case.jurisdiction,
                case.scenario_type,
                _date_to_db(case.assessment_date),
                case.status,
                case.owner_id,
                case.reviewer_id,
                case.active_assessment_id,
                case.created_at,
                case.updated_at,
                case.case_id,
            ),
        )
        conn.commit()


def _case_values(case: Case) -> tuple[object, ...]:
    return (
        case.case_id,
        case.workspace_id,
        case.title,
        case.description,
        case.jurisdiction,
        case.scenario_type,
        _date_to_db(case.assessment_date),
        case.status,
        case.owner_id,
        case.reviewer_id,
        case.active_assessment_id,
        case.created_at,
        case.updated_at,
    )


def _date_to_db(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def _row_to_case(row: Any) -> Case:
    raw_assessment_date = row["assessment_date"]
    return Case(
        case_id=row["case_id"],
        workspace_id=row["workspace_id"],
        title=row["title"],
        description=row["description"],
        jurisdiction=row["jurisdiction"],
        scenario_type=row["scenario_type"],
        assessment_date=(
            None if raw_assessment_date is None else date.fromisoformat(raw_assessment_date)
        ),
        status=_validate_case_status(row["status"]),
        owner_id=row["owner_id"],
        reviewer_id=row["reviewer_id"],
        active_assessment_id=row["active_assessment_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _validate_case_status(value: str) -> CaseStatus:
    valid = {
        "draft",
        "collecting",
        "processing_documents",
        "facts_pending_confirmation",
        "ready_for_assessment",
        "assessing",
        "review_required",
        "completed",
        "archived",
    }
    if value not in valid:
        raise ValueError(f"invalid case status in DB: {value!r}")
    return value  # type: ignore[return-value]
