"""Owner-bound risk results attached to a conversation run."""

from __future__ import annotations

from domain.risk import RiskAssessment
from infra.storage._db import SqliteConnectionPool


class SqliteRiskAssessmentRepo:
    def __init__(self, pool: SqliteConnectionPool) -> None:
        self._pool = pool
        self._pool.get().execute("""
            CREATE TABLE IF NOT EXISTS conversation_risk_assessments (
                run_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, payload TEXT NOT NULL
            )
        """)
        self._pool.get().commit()

    def save(self, assessment: RiskAssessment) -> None:
        conn = self._pool.get()
        with conn:
            conn.execute(
                "INSERT INTO conversation_risk_assessments VALUES (?, ?, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET payload=excluded.payload "
                "WHERE owner_id=excluded.owner_id",
                (assessment.run_id, assessment.owner_id, assessment.model_dump_json()),
            )

    def for_run(self, owner_id: str, run_id: str) -> RiskAssessment | None:
        row = (
            self._pool.get()
            .execute(
                "SELECT payload FROM conversation_risk_assessments WHERE owner_id=? AND run_id=?",
                (owner_id, run_id),
            )
            .fetchone()
        )
        return RiskAssessment.model_validate_json(row[0]) if row else None
