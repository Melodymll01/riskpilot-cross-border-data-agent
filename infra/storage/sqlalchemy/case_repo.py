"""SQLAlchemy CaseRepoPort 实现。"""

from __future__ import annotations

from sqlalchemy import select

from domain.cases import Case
from infra.storage.sqlalchemy.database import SqlAlchemyDatabase
from infra.storage.sqlalchemy.mapping import require_datetime, require_timestamp
from infra.storage.sqlalchemy.models import CaseRow


class SqlAlchemyCaseRepo:
    def __init__(self, database: SqlAlchemyDatabase) -> None:
        self._database = database

    def create(self, case: Case) -> None:
        with self._database.session() as session:
            session.add(_row(case))

    def get(self, case_id: str) -> Case | None:
        with self._database.read_session() as session:
            row = session.get(CaseRow, case_id)
            return None if row is None else _case(row)

    def list_for_workspace(
        self,
        workspace_id: str,
        *,
        include_archived: bool = False,
        limit: int = 50,
    ) -> list[Case]:
        statement = select(CaseRow).where(CaseRow.workspace_id == workspace_id)
        if not include_archived:
            statement = statement.where(CaseRow.status != "archived")
        statement = statement.order_by(CaseRow.updated_at.desc()).limit(limit)
        with self._database.read_session() as session:
            return [_case(row) for row in session.scalars(statement)]

    def update(self, case: Case) -> None:
        with self._database.session() as session:
            row = session.get(CaseRow, case.case_id)
            if row is None:
                raise ValueError("待更新 Case 不存在")
            for key, value in _values(case).items():
                setattr(row, key, value)


def _values(case: Case) -> dict[str, object]:
    return {
        "workspace_id": case.workspace_id,
        "title": case.title,
        "description": case.description,
        "jurisdiction": case.jurisdiction,
        "scenario_type": case.scenario_type,
        "assessment_date": case.assessment_date,
        "status": case.status,
        "owner_id": case.owner_id,
        "reviewer_id": case.reviewer_id,
        "active_assessment_id": case.active_assessment_id,
        "created_at": require_datetime(case.created_at),
        "updated_at": require_datetime(case.updated_at),
    }


def _row(case: Case) -> CaseRow:
    return CaseRow(case_id=case.case_id, **_values(case))


def _case(row: CaseRow) -> Case:
    return Case(
        case_id=row.case_id,
        workspace_id=row.workspace_id,
        title=row.title,
        description=row.description,
        jurisdiction=row.jurisdiction,
        scenario_type=row.scenario_type,
        assessment_date=row.assessment_date,
        status=row.status,
        owner_id=row.owner_id,
        reviewer_id=row.reviewer_id,
        active_assessment_id=row.active_assessment_id,
        created_at=require_timestamp(row.created_at),
        updated_at=require_timestamp(row.updated_at),
    )
