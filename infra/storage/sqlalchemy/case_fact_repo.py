"""SQLAlchemy CaseFactRepoPort 实现。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from domain.facts import CaseFact, CaseFactEvidence
from infra.storage.sqlalchemy.database import SqlAlchemyDatabase
from infra.storage.sqlalchemy.mapping import (
    require_datetime,
    require_timestamp,
    to_datetime,
    to_timestamp,
)
from infra.storage.sqlalchemy.models import (
    CaseDocumentRow,
    CaseFactEvidenceRow,
    CaseFactRow,
    CaseFactVersionRow,
    DocumentVersionRow,
)


class SqlAlchemyCaseFactRepo:
    def __init__(self, database: SqlAlchemyDatabase) -> None:
        self._database = database

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
        with self._database.session() as session:
            for fact, evidence in items:
                _validate_persisted_scope(session, evidence)
                session.add(_fact_row(fact))
                session.add(_version_row(fact))
                session.add_all(_evidence_row(item) for item in evidence)

    def get(self, fact_id: str) -> CaseFact | None:
        with self._database.read_session() as session:
            row = session.get(CaseFactRow, fact_id)
            return None if row is None else _fact(row)

    def get_version(self, fact_id: str, version: int) -> CaseFact | None:
        with self._database.read_session() as session:
            row = session.get(CaseFactVersionRow, (fact_id, version))
            return None if row is None else CaseFact.model_validate(row.payload)

    def list_for_case(
        self,
        case_id: str,
        *,
        statuses: set[str] | None = None,
    ) -> list[CaseFact]:
        statement = select(CaseFactRow).where(CaseFactRow.case_id == case_id)
        if statuses:
            statement = statement.where(CaseFactRow.status.in_(sorted(statuses)))
        statement = statement.order_by(
            CaseFactRow.updated_at.desc(),
            CaseFactRow.fact_id,
        )
        with self._database.read_session() as session:
            return [_fact(row) for row in session.scalars(statement)]

    def list_evidence(
        self,
        fact_id: str,
        *,
        fact_version: int | None = None,
    ) -> list[CaseFactEvidence]:
        statement = select(CaseFactEvidenceRow).where(CaseFactEvidenceRow.fact_id == fact_id)
        if fact_version is not None:
            statement = statement.where(CaseFactEvidenceRow.fact_version == fact_version)
        statement = statement.order_by(
            CaseFactEvidenceRow.fact_version.desc(),
            CaseFactEvidenceRow.created_at,
            CaseFactEvidenceRow.evidence_id,
        )
        with self._database.read_session() as session:
            return [_evidence(row) for row in session.scalars(statement)]

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
        with self._database.session() as session:
            _validate_persisted_scope(session, evidence)
            row = session.get(CaseFactRow, fact.fact_id)
            if row is None:
                raise ValueError("待修订事实不存在")
            _apply_fact(row, fact)
            session.add(_version_row(fact))
            session.add_all(_evidence_row(item) for item in evidence)

    def update_status(self, fact: CaseFact) -> None:
        self.update_statuses([fact])

    def update_statuses(self, facts: list[CaseFact]) -> None:
        if not facts:
            return
        fact_ids = [fact.fact_id for fact in facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("批量状态更新 Fact ID 不能重复")
        with self._database.session() as session:
            for fact in facts:
                row = session.get(CaseFactRow, fact.fact_id)
                version = session.get(
                    CaseFactVersionRow,
                    (fact.fact_id, fact.version),
                )
                if row is None or version is None:
                    raise ValueError("待更新事实不存在")
                if row.version != fact.version:
                    raise ValueError("状态更新不能改变事实版本")
                _apply_fact(row, fact)
                version.payload = fact.model_dump(mode="json")


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


def _validate_persisted_scope(
    session: Session,
    evidence: list[CaseFactEvidence],
) -> None:
    for item in evidence:
        statement = (
            select(DocumentVersionRow.document_id)
            .join(
                CaseDocumentRow,
                CaseDocumentRow.document_id == DocumentVersionRow.document_id,
            )
            .where(
                DocumentVersionRow.version_id == item.document_version_id,
                CaseDocumentRow.case_id == item.case_id,
            )
        )
        document_id = session.scalar(statement)
        if document_id is None:
            raise ValueError("证据版本未绑定当前 Case")
        if document_id != item.document_id:
            raise ValueError("证据 document_id 与 DocumentVersion 不一致")


def _fact_row(fact: CaseFact) -> CaseFactRow:
    row = CaseFactRow(fact_id=fact.fact_id)
    _apply_fact(row, fact)
    return row


def _apply_fact(row: CaseFactRow, fact: CaseFact) -> None:
    row.case_id = fact.case_id
    row.field_name = fact.field_name
    row.value = fact.value
    row.status = fact.status
    row.source_type = fact.source_type
    row.confidence = fact.confidence
    row.criticality = fact.criticality
    row.version = fact.version
    row.created_by = fact.created_by
    row.confirmed_by = fact.confirmed_by
    row.confirmed_at = to_datetime(fact.confirmed_at)
    row.created_at = require_datetime(fact.created_at)
    row.updated_at = require_datetime(fact.updated_at)


def _version_row(fact: CaseFact) -> CaseFactVersionRow:
    return CaseFactVersionRow(
        fact_id=fact.fact_id,
        version=fact.version,
        payload=fact.model_dump(mode="json"),
        created_at=require_datetime(fact.updated_at),
    )


def _evidence_row(evidence: CaseFactEvidence) -> CaseFactEvidenceRow:
    return CaseFactEvidenceRow(
        evidence_id=evidence.evidence_id,
        case_id=evidence.case_id,
        fact_id=evidence.fact_id,
        fact_version=evidence.fact_version,
        document_id=evidence.document_id,
        document_version_id=evidence.document_version_id,
        page_number=evidence.page_number,
        quote=evidence.quote,
        start_offset=evidence.start_offset,
        end_offset=evidence.end_offset,
        confidence=evidence.confidence,
        created_at=require_datetime(evidence.created_at),
    )


def _fact(row: CaseFactRow) -> CaseFact:
    return CaseFact(
        fact_id=row.fact_id,
        case_id=row.case_id,
        field_name=row.field_name,
        value=row.value,
        status=row.status,
        source_type=row.source_type,
        confidence=row.confidence,
        criticality=row.criticality,
        version=row.version,
        created_by=row.created_by,
        confirmed_by=row.confirmed_by,
        confirmed_at=to_timestamp(row.confirmed_at),
        created_at=require_timestamp(row.created_at),
        updated_at=require_timestamp(row.updated_at),
    )


def _evidence(row: CaseFactEvidenceRow) -> CaseFactEvidence:
    return CaseFactEvidence(
        evidence_id=row.evidence_id,
        case_id=row.case_id,
        fact_id=row.fact_id,
        fact_version=row.fact_version,
        document_id=row.document_id,
        document_version_id=row.document_version_id,
        page_number=row.page_number,
        quote=row.quote,
        start_offset=row.start_offset,
        end_offset=row.end_offset,
        confidence=row.confidence,
        created_at=require_timestamp(row.created_at),
    )
