"""SQLAlchemy AssessmentRepoPort 实现。"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult

from domain.assessments import (
    ActionItem,
    Assessment,
    AssessmentBundle,
    AssessmentEvidenceCitation,
    Finding,
)
from domain.cases import Case
from domain.policies import PolicyEvaluation
from infra.storage.sqlalchemy.database import SqlAlchemyDatabase
from infra.storage.sqlalchemy.mapping import (
    require_datetime,
    require_timestamp,
    to_datetime,
    to_timestamp,
)
from infra.storage.sqlalchemy.models import (
    ActionItemRow,
    AssessmentEvidenceCitationRow,
    AssessmentRow,
    CaseRow,
    FindingRow,
)


class SqlAlchemyAssessmentRepo:
    def __init__(self, database: SqlAlchemyDatabase) -> None:
        self._database = database

    def create_version(
        self,
        bundle: AssessmentBundle,
        previous: Assessment | None,
        case: Case,
    ) -> None:
        assessment = bundle.assessment
        _validate_version(assessment, previous, case)
        with self._database.session() as session:
            if previous is not None:
                previous_result = cast(
                    "CursorResult[Any]",
                    session.execute(
                        update(AssessmentRow)
                        .where(
                            AssessmentRow.assessment_id == previous.assessment_id,
                            AssessmentRow.status != "superseded",
                        )
                        .values(
                            status=previous.status,
                            approved_by=previous.approved_by,
                            approved_at=to_datetime(previous.approved_at),
                            review_comment=previous.review_comment,
                            updated_at=require_datetime(previous.updated_at),
                        )
                    ),
                )
                if previous_result.rowcount != 1:
                    raise ValueError("旧 Assessment 状态已变化，请重新生成")
            session.add(_assessment_row(assessment))
            session.add_all(_citation_row(item) for item in bundle.evidence_citations)
            session.add_all(_finding_row(item) for item in bundle.findings)
            session.add_all(_action_row(item) for item in bundle.action_items)
            case_statement = update(CaseRow).where(CaseRow.case_id == case.case_id)
            if previous is None:
                case_statement = case_statement.where(CaseRow.active_assessment_id.is_(None))
            else:
                case_statement = case_statement.where(
                    CaseRow.active_assessment_id == previous.assessment_id
                )
            result = cast(
                "CursorResult[Any]",
                session.execute(
                    case_statement.values(
                        status=case.status,
                        active_assessment_id=case.active_assessment_id,
                        updated_at=require_datetime(case.updated_at),
                    )
                ),
            )
            if result.rowcount != 1:
                raise ValueError("Case 活动 Assessment 已变化，请重新生成")

    def get(self, assessment_id: str) -> AssessmentBundle | None:
        with self._database.read_session() as session:
            assessment_row = session.get(AssessmentRow, assessment_id)
            if assessment_row is None:
                return None
            findings = list(
                session.scalars(
                    select(FindingRow)
                    .where(FindingRow.assessment_id == assessment_id)
                    .order_by(FindingRow.finding_id)
                )
            )
            actions = list(
                session.scalars(
                    select(ActionItemRow)
                    .where(ActionItemRow.assessment_id == assessment_id)
                    .order_by(ActionItemRow.action_id)
                )
            )
            citations = list(
                session.scalars(
                    select(AssessmentEvidenceCitationRow)
                    .where(AssessmentEvidenceCitationRow.assessment_id == assessment_id)
                    .order_by(AssessmentEvidenceCitationRow.citation_id)
                )
            )
            return AssessmentBundle(
                assessment=_assessment(assessment_row),
                findings=[_finding(row) for row in findings],
                action_items=[_action(row) for row in actions],
                evidence_citations=[_citation(row) for row in citations],
            )

    def get_active(self, case_id: str) -> AssessmentBundle | None:
        statement = select(CaseRow.active_assessment_id).where(CaseRow.case_id == case_id)
        with self._database.read_session() as session:
            assessment_id = session.scalar(statement)
        return None if assessment_id is None else self.get(assessment_id)

    def list_for_case(self, case_id: str) -> list[Assessment]:
        statement = (
            select(AssessmentRow)
            .where(AssessmentRow.case_id == case_id)
            .order_by(AssessmentRow.version.desc())
        )
        with self._database.read_session() as session:
            return [_assessment(row) for row in session.scalars(statement)]

    def next_version(self, case_id: str) -> int:
        statement = select(func.coalesce(func.max(AssessmentRow.version), 0) + 1).where(
            AssessmentRow.case_id == case_id
        )
        with self._database.read_session() as session:
            return int(session.execute(statement).scalar_one())

    def save_review(self, assessment: Assessment, case: Case) -> None:
        if assessment.case_id != case.case_id:
            raise ValueError("Assessment 必须属于当前 Case")
        if case.active_assessment_id != assessment.assessment_id:
            raise ValueError("只能审批 Case 当前活动的 Assessment")
        with self._database.session() as session:
            result = cast(
                "CursorResult[Any]",
                session.execute(
                    update(AssessmentRow)
                    .where(
                        AssessmentRow.assessment_id == assessment.assessment_id,
                        AssessmentRow.status == "review_required",
                    )
                    .values(
                        status=assessment.status,
                        approved_by=assessment.approved_by,
                        approved_at=to_datetime(assessment.approved_at),
                        review_comment=assessment.review_comment,
                        updated_at=require_datetime(assessment.updated_at),
                    )
                ),
            )
            if result.rowcount != 1:
                raise ValueError("Assessment 审批状态已变化，请刷新后重试")
            result = cast(
                "CursorResult[Any]",
                session.execute(
                    update(CaseRow)
                    .where(
                        CaseRow.case_id == case.case_id,
                        CaseRow.active_assessment_id == assessment.assessment_id,
                        CaseRow.status == "review_required",
                    )
                    .values(
                        status=case.status,
                        active_assessment_id=case.active_assessment_id,
                        updated_at=require_datetime(case.updated_at),
                    )
                ),
            )
            if result.rowcount != 1:
                raise ValueError("Case 活动 Assessment 已变化，请刷新后重试")


def _validate_version(
    assessment: Assessment,
    previous: Assessment | None,
    case: Case,
) -> None:
    if assessment.case_id != case.case_id:
        raise ValueError("Assessment 必须属于当前 Case")
    if case.active_assessment_id != assessment.assessment_id:
        raise ValueError("Case.active_assessment_id 必须指向新 Assessment")
    if previous is None:
        if assessment.version != 1:
            raise ValueError("首个 Assessment 版本必须为 1")
        return
    if previous.case_id != assessment.case_id:
        raise ValueError("旧 Assessment 必须属于同一 Case")
    if assessment.version != previous.version + 1:
        raise ValueError("Assessment 版本必须单调递增 1")
    if previous.status != "superseded":
        raise ValueError("创建新版本前旧 Assessment 必须标记 superseded")


def _assessment_row(assessment: Assessment) -> AssessmentRow:
    return AssessmentRow(
        assessment_id=assessment.assessment_id,
        case_id=assessment.case_id,
        version=assessment.version,
        status=assessment.status,
        assessment_date=assessment.assessment_date,
        jurisdiction=assessment.jurisdiction,
        ruleset_version=assessment.ruleset_version,
        fact_versions=assessment.fact_versions,
        policy_evaluations=[item.model_dump(mode="json") for item in assessment.policy_evaluations],
        risk_level=assessment.risk_level,
        candidate_paths=assessment.candidate_paths,
        generated_by_run_id=assessment.generated_by_run_id,
        approved_by=assessment.approved_by,
        approved_at=to_datetime(assessment.approved_at),
        review_comment=assessment.review_comment,
        created_at=require_datetime(assessment.created_at),
        updated_at=require_datetime(assessment.updated_at),
    )


def _citation_row(
    citation: AssessmentEvidenceCitation,
) -> AssessmentEvidenceCitationRow:
    return AssessmentEvidenceCitationRow(
        citation_id=citation.citation_id,
        assessment_id=citation.assessment_id,
        source_evidence_id=citation.source_evidence_id,
        fact_id=citation.fact_id,
        fact_version=citation.fact_version,
        document_id=citation.document_id,
        document_version_id=citation.document_version_id,
        page_number=citation.page_number,
        quote=citation.quote,
        start_offset=citation.start_offset,
        end_offset=citation.end_offset,
        source_sha256=citation.source_sha256,
        created_at=require_datetime(citation.created_at),
    )


def _finding_row(finding: Finding) -> FindingRow:
    return FindingRow(
        finding_id=finding.finding_id,
        assessment_id=finding.assessment_id,
        finding_type=finding.finding_type,
        severity=finding.severity,
        title=finding.title,
        description=finding.description,
        fact_ids=finding.fact_ids,
        evidence_ids=finding.evidence_ids,
        clause_ids=finding.clause_ids,
        rule_ids=finding.rule_ids,
        status=finding.status,
    )


def _action_row(action: ActionItem) -> ActionItemRow:
    return ActionItemRow(
        action_id=action.action_id,
        assessment_id=action.assessment_id,
        title=action.title,
        description=action.description,
        priority=action.priority,
        owner_id=action.owner_id,
        due_at=to_datetime(action.due_at),
        status=action.status,
        related_finding_ids=action.related_finding_ids,
    )


def _assessment(row: AssessmentRow) -> Assessment:
    return Assessment(
        assessment_id=row.assessment_id,
        case_id=row.case_id,
        version=row.version,
        status=row.status,
        assessment_date=row.assessment_date,
        jurisdiction=row.jurisdiction,
        ruleset_version=row.ruleset_version,
        fact_versions=row.fact_versions,
        policy_evaluations=[
            PolicyEvaluation.model_validate(item) for item in row.policy_evaluations
        ],
        risk_level=row.risk_level,
        candidate_paths=row.candidate_paths,
        generated_by_run_id=row.generated_by_run_id,
        approved_by=row.approved_by,
        approved_at=to_timestamp(row.approved_at),
        review_comment=row.review_comment,
        created_at=require_timestamp(row.created_at),
        updated_at=require_timestamp(row.updated_at),
    )


def _citation(
    row: AssessmentEvidenceCitationRow,
) -> AssessmentEvidenceCitation:
    return AssessmentEvidenceCitation(
        citation_id=row.citation_id,
        assessment_id=row.assessment_id,
        source_evidence_id=row.source_evidence_id,
        fact_id=row.fact_id,
        fact_version=row.fact_version,
        document_id=row.document_id,
        document_version_id=row.document_version_id,
        page_number=row.page_number,
        quote=row.quote,
        start_offset=row.start_offset,
        end_offset=row.end_offset,
        source_sha256=row.source_sha256,
        created_at=require_timestamp(row.created_at),
    )


def _finding(row: FindingRow) -> Finding:
    return Finding(
        finding_id=row.finding_id,
        assessment_id=row.assessment_id,
        finding_type=row.finding_type,
        severity=row.severity,
        title=row.title,
        description=row.description,
        fact_ids=row.fact_ids,
        evidence_ids=row.evidence_ids,
        clause_ids=row.clause_ids,
        rule_ids=row.rule_ids,
        status=row.status,
    )


def _action(row: ActionItemRow) -> ActionItem:
    return ActionItem(
        action_id=row.action_id,
        assessment_id=row.assessment_id,
        title=row.title,
        description=row.description,
        priority=row.priority,
        owner_id=row.owner_id,
        due_at=to_timestamp(row.due_at),
        status=row.status,
        related_finding_ids=row.related_finding_ids,
    )
