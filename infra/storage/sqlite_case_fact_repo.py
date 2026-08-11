"""SQLite CaseFactRepoPort 实现。"""

from __future__ import annotations

import json
from typing import Any, cast

from domain.facts import (
    CaseFact,
    CaseFactEvidence,
    CaseFactSource,
    CaseFactStatus,
    FactCriticality,
)
from infra.storage._db import SqliteConnectionPool


class SqliteCaseFactRepo:
    def __init__(self, pool: SqliteConnectionPool) -> None:
        self._pool = pool

    def create(
        self,
        fact: CaseFact,
        evidence: list[CaseFactEvidence],
    ) -> None:
        self.create_many([(fact, evidence)])

    def create_many(
        self,
        items: list[tuple[CaseFact, list[CaseFactEvidence]]],
    ) -> None:
        for fact, evidence in items:
            _validate_evidence(fact, evidence)
        conn = self._pool.get()
        with conn:
            for fact, evidence in items:
                _validate_persisted_evidence_scope(conn, evidence)
                _insert_fact(conn, fact)
                _insert_version(conn, fact)
                _insert_evidence(conn, evidence)

    def get(self, fact_id: str) -> CaseFact | None:
        row = (
            self._pool.get()
            .execute(
                "SELECT * FROM case_facts WHERE fact_id = ?",
                (fact_id,),
            )
            .fetchone()
        )
        return None if row is None else _row_to_fact(row)

    def get_version(self, fact_id: str, version: int) -> CaseFact | None:
        row = (
            self._pool.get()
            .execute(
                """
            SELECT payload_json FROM case_fact_versions
            WHERE fact_id = ? AND version = ?
            """,
                (fact_id, version),
            )
            .fetchone()
        )
        if row is None:
            return None
        return CaseFact.model_validate(json.loads(row["payload_json"]))

    def list_for_case(
        self,
        case_id: str,
        *,
        statuses: set[str] | None = None,
    ) -> list[CaseFact]:
        conn = self._pool.get()
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            rows = conn.execute(
                f"""
                SELECT * FROM case_facts
                WHERE case_id = ? AND status IN ({placeholders})
                ORDER BY updated_at DESC, fact_id
                """,
                (case_id, *sorted(statuses)),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM case_facts
                WHERE case_id = ?
                ORDER BY updated_at DESC, fact_id
                """,
                (case_id,),
            ).fetchall()
        return [_row_to_fact(row) for row in rows]

    def list_evidence(
        self,
        fact_id: str,
        *,
        fact_version: int | None = None,
    ) -> list[CaseFactEvidence]:
        conn = self._pool.get()
        if fact_version is None:
            rows = conn.execute(
                """
                SELECT * FROM case_fact_evidence
                WHERE fact_id = ?
                ORDER BY fact_version DESC, created_at, evidence_id
                """,
                (fact_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM case_fact_evidence
                WHERE fact_id = ? AND fact_version = ?
                ORDER BY created_at, evidence_id
                """,
                (fact_id, fact_version),
            ).fetchall()
        return [_row_to_evidence(row) for row in rows]

    def save_revision(
        self,
        fact: CaseFact,
        evidence: list[CaseFactEvidence],
    ) -> None:
        current = self.get(fact.fact_id)
        if current is None:
            raise ValueError("待修订事实不存在")
        if fact.case_id != current.case_id:
            raise ValueError("事实修订不能跨 Case")
        if fact.version != current.version + 1:
            raise ValueError("事实版本必须单调递增 1")
        _validate_evidence(fact, evidence)
        conn = self._pool.get()
        with conn:
            _validate_persisted_evidence_scope(conn, evidence)
            _update_fact(conn, fact)
            _insert_version(conn, fact)
            _insert_evidence(conn, evidence)

    def update_status(self, fact: CaseFact) -> None:
        self.update_statuses([fact])

    def update_statuses(self, facts: list[CaseFact]) -> None:
        if not facts:
            return
        fact_ids = [fact.fact_id for fact in facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("批量状态更新 Fact ID 不能重复")
        currents = {fact_id: self.get(fact_id) for fact_id in fact_ids}
        for fact in facts:
            current = currents[fact.fact_id]
            if current is None:
                raise ValueError("待更新事实不存在")
            if fact.version != current.version:
                raise ValueError("状态更新不能改变事实版本")
        conn = self._pool.get()
        with conn:
            for fact in facts:
                _update_fact(conn, fact)
                conn.execute(
                    """
                    UPDATE case_fact_versions SET payload_json = ?
                    WHERE fact_id = ? AND version = ?
                    """,
                    (fact.model_dump_json(), fact.fact_id, fact.version),
                )


def _validate_evidence(
    fact: CaseFact,
    evidence: list[CaseFactEvidence],
) -> None:
    for item in evidence:
        if (
            item.fact_id != fact.fact_id
            or item.case_id != fact.case_id
            or item.fact_version != fact.version
        ):
            raise ValueError("证据必须属于当前事实及其版本")


def _insert_fact(conn: Any, fact: CaseFact) -> None:
    conn.execute(
        """
        INSERT INTO case_facts
            (fact_id, case_id, field_name, value_json, status, source_type,
             confidence, criticality, version, created_by, confirmed_by,
             confirmed_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        _fact_values(fact),
    )


def _validate_persisted_evidence_scope(
    conn: Any,
    evidence: list[CaseFactEvidence],
) -> None:
    for item in evidence:
        row = conn.execute(
            """
            SELECT dv.document_id AS version_document_id
            FROM document_versions AS dv
            JOIN case_documents AS cd
              ON cd.document_id = dv.document_id AND cd.case_id = ?
            WHERE dv.version_id = ?
            """,
            (item.case_id, item.document_version_id),
        ).fetchone()
        if row is None:
            raise ValueError("证据版本未绑定当前 Case")
        if row["version_document_id"] != item.document_id:
            raise ValueError("证据 document_id 与 DocumentVersion 不一致")


def _update_fact(conn: Any, fact: CaseFact) -> None:
    conn.execute(
        """
        UPDATE case_facts SET
            case_id = ?, field_name = ?, value_json = ?, status = ?,
            source_type = ?, confidence = ?, criticality = ?, version = ?,
            created_by = ?, confirmed_by = ?, confirmed_at = ?,
            created_at = ?, updated_at = ?
        WHERE fact_id = ?
        """,
        (*_fact_values(fact)[1:], fact.fact_id),
    )


def _fact_values(fact: CaseFact) -> tuple[object, ...]:
    return (
        fact.fact_id,
        fact.case_id,
        fact.field_name,
        json.dumps(fact.value, ensure_ascii=False),
        fact.status,
        fact.source_type,
        fact.confidence,
        fact.criticality,
        fact.version,
        fact.created_by,
        fact.confirmed_by,
        fact.confirmed_at,
        fact.created_at,
        fact.updated_at,
    )


def _insert_version(conn: Any, fact: CaseFact) -> None:
    conn.execute(
        """
        INSERT INTO case_fact_versions
            (fact_id, version, payload_json, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (fact.fact_id, fact.version, fact.model_dump_json(), fact.updated_at),
    )


def _insert_evidence(conn: Any, evidence: list[CaseFactEvidence]) -> None:
    conn.executemany(
        """
        INSERT INTO case_fact_evidence
            (evidence_id, case_id, fact_id, fact_version, document_id,
             document_version_id, page_number, quote, start_offset, end_offset,
             confidence, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                item.evidence_id,
                item.case_id,
                item.fact_id,
                item.fact_version,
                item.document_id,
                item.document_version_id,
                item.page_number,
                item.quote,
                item.start_offset,
                item.end_offset,
                item.confidence,
                item.created_at,
            )
            for item in evidence
        ],
    )


def _row_to_fact(row: Any) -> CaseFact:
    return CaseFact(
        fact_id=row["fact_id"],
        case_id=row["case_id"],
        field_name=row["field_name"],
        value=json.loads(row["value_json"]),
        status=_validate_fact_status(row["status"]),
        source_type=_validate_fact_source(row["source_type"]),
        confidence=row["confidence"],
        criticality=_validate_criticality(row["criticality"]),
        version=row["version"],
        created_by=row["created_by"],
        confirmed_by=row["confirmed_by"],
        confirmed_at=row["confirmed_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_evidence(row: Any) -> CaseFactEvidence:
    return CaseFactEvidence(
        evidence_id=row["evidence_id"],
        case_id=row["case_id"],
        fact_id=row["fact_id"],
        fact_version=row["fact_version"],
        document_id=row["document_id"],
        document_version_id=row["document_version_id"],
        page_number=row["page_number"],
        quote=row["quote"],
        start_offset=row["start_offset"],
        end_offset=row["end_offset"],
        confidence=row["confidence"],
        created_at=row["created_at"],
    )


def _validate_fact_status(value: str) -> CaseFactStatus:
    if value not in {"proposed", "confirmed", "rejected", "conflicting", "unknown"}:
        raise ValueError(f"invalid case fact status in DB: {value!r}")
    return cast("CaseFactStatus", value)


def _validate_fact_source(value: str) -> CaseFactSource:
    if value not in {"user", "document", "system", "import"}:
        raise ValueError(f"invalid case fact source in DB: {value!r}")
    return cast("CaseFactSource", value)


def _validate_criticality(value: str) -> FactCriticality:
    if value not in {"normal", "critical"}:
        raise ValueError(f"invalid case fact criticality in DB: {value!r}")
    return cast("FactCriticality", value)
