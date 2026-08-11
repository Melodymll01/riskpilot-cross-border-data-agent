"""SQLite AssessmentRepoPort 实现。"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, cast

from domain.assessments import (
    ActionItem,
    ActionPriority,
    ActionStatus,
    Assessment,
    AssessmentBundle,
    AssessmentEvidenceCitation,
    AssessmentStatus,
    Finding,
    FindingSeverity,
    FindingStatus,
    FindingType,
    RiskLevel,
)
from domain.cases import Case
from domain.policies import PolicyEvaluation
from infra.storage._db import SqliteConnectionPool


class SqliteAssessmentRepo:
    def __init__(self, pool: SqliteConnectionPool) -> None:
        self._pool = pool

    def create_version(
        self,
        bundle: AssessmentBundle,
        previous: Assessment | None,
        case: Case,
    ) -> None:
        assessment = bundle.assessment
        if assessment.case_id != case.case_id:
            raise ValueError("Assessment 必须属于当前 Case")
        if case.active_assessment_id != assessment.assessment_id:
            raise ValueError("Case.active_assessment_id 必须指向新 Assessment")
        if previous is None:
            if assessment.version != 1:
                raise ValueError("首个 Assessment 版本必须为 1")
        else:
            if previous.case_id != assessment.case_id:
                raise ValueError("旧 Assessment 必须属于同一 Case")
            if assessment.version != previous.version + 1:
                raise ValueError("Assessment 版本必须单调递增 1")
            if previous.status != "superseded":
                raise ValueError("创建新版本前旧 Assessment 必须标记 superseded")

        conn = self._pool.get()
        with conn:
            if previous is not None:
                _update_assessment_status(conn, previous)
            conn.execute(
                """
                INSERT INTO assessments
                    (assessment_id, case_id, version, status, assessment_date,
                     jurisdiction, ruleset_version, fact_versions_json,
                     policy_evaluations_json, risk_level, candidate_paths_json,
                     generated_by_run_id, approved_by, approved_at, review_comment,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _assessment_values(assessment),
            )
            conn.executemany(
                """
                INSERT INTO assessment_evidence_citations
                    (citation_id, assessment_id, source_evidence_id, fact_id,
                     fact_version, document_id, document_version_id, page_number,
                     quote, start_offset, end_offset, source_sha256, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [_evidence_citation_values(citation) for citation in bundle.evidence_citations],
            )
            conn.executemany(
                """
                INSERT INTO assessment_findings
                    (finding_id, assessment_id, finding_type, severity, title,
                     description, fact_ids_json, evidence_ids_json, clause_ids_json,
                     rule_ids_json, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [_finding_values(finding) for finding in bundle.findings],
            )
            conn.executemany(
                """
                INSERT INTO assessment_actions
                    (action_id, assessment_id, title, description, priority,
                     owner_id, due_at, status, related_finding_ids_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [_action_values(action) for action in bundle.action_items],
            )
            conn.execute(
                """
                UPDATE compliance_cases SET
                    status = ?,
                    active_assessment_id = ?,
                    updated_at = ?
                WHERE case_id = ?
                  AND (
                    active_assessment_id = ?
                    OR (active_assessment_id IS NULL AND ? IS NULL)
                  )
                """,
                (
                    case.status,
                    case.active_assessment_id,
                    case.updated_at,
                    case.case_id,
                    None if previous is None else previous.assessment_id,
                    None if previous is None else previous.assessment_id,
                ),
            )
            if conn.execute("SELECT changes()").fetchone()[0] != 1:
                raise ValueError("Case 活动 Assessment 已变化，请重新生成")

    def get(self, assessment_id: str) -> AssessmentBundle | None:
        conn = self._pool.get()
        row = conn.execute(
            "SELECT * FROM assessments WHERE assessment_id = ?",
            (assessment_id,),
        ).fetchone()
        if row is None:
            return None
        return AssessmentBundle(
            assessment=_row_to_assessment(row),
            findings=[
                _row_to_finding(item)
                for item in conn.execute(
                    """
                    SELECT * FROM assessment_findings
                    WHERE assessment_id = ?
                    ORDER BY finding_id
                    """,
                    (assessment_id,),
                ).fetchall()
            ],
            action_items=[
                _row_to_action(item)
                for item in conn.execute(
                    """
                    SELECT * FROM assessment_actions
                    WHERE assessment_id = ?
                    ORDER BY action_id
                    """,
                    (assessment_id,),
                ).fetchall()
            ],
            evidence_citations=[
                _row_to_evidence_citation(item)
                for item in conn.execute(
                    """
                    SELECT * FROM assessment_evidence_citations
                    WHERE assessment_id = ?
                    ORDER BY citation_id
                    """,
                    (assessment_id,),
                ).fetchall()
            ],
        )

    def get_active(self, case_id: str) -> AssessmentBundle | None:
        row = (
            self._pool.get()
            .execute(
                """
            SELECT a.assessment_id
            FROM compliance_cases AS c
            JOIN assessments AS a ON a.assessment_id = c.active_assessment_id
            WHERE c.case_id = ?
            """,
                (case_id,),
            )
            .fetchone()
        )
        return None if row is None else self.get(row["assessment_id"])

    def list_for_case(self, case_id: str) -> list[Assessment]:
        rows = (
            self._pool.get()
            .execute(
                """
            SELECT * FROM assessments
            WHERE case_id = ?
            ORDER BY version DESC
            """,
                (case_id,),
            )
            .fetchall()
        )
        return [_row_to_assessment(row) for row in rows]

    def next_version(self, case_id: str) -> int:
        row = (
            self._pool.get()
            .execute(
                """
            SELECT COALESCE(MAX(version), 0) + 1 AS next_version
            FROM assessments
            WHERE case_id = ?
            """,
                (case_id,),
            )
            .fetchone()
        )
        return int(row["next_version"])

    def save_review(self, assessment: Assessment, case: Case) -> None:
        if assessment.case_id != case.case_id:
            raise ValueError("Assessment 必须属于当前 Case")
        if case.active_assessment_id != assessment.assessment_id:
            raise ValueError("只能审批 Case 当前活动的 Assessment")
        conn = self._pool.get()
        with conn:
            cursor = conn.execute(
                """
                UPDATE assessments SET
                    status = ?, approved_by = ?, approved_at = ?,
                    review_comment = ?, updated_at = ?
                WHERE assessment_id = ? AND status = 'review_required'
                """,
                (
                    assessment.status,
                    assessment.approved_by,
                    assessment.approved_at,
                    assessment.review_comment,
                    assessment.updated_at,
                    assessment.assessment_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("Assessment 审批状态已变化，请刷新后重试")
            cursor = conn.execute(
                """
                UPDATE compliance_cases SET
                    status = ?,
                    active_assessment_id = ?,
                    updated_at = ?
                WHERE case_id = ?
                  AND active_assessment_id = ?
                  AND status = 'review_required'
                """,
                (
                    case.status,
                    case.active_assessment_id,
                    case.updated_at,
                    case.case_id,
                    assessment.assessment_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("Case 活动 Assessment 已变化，请刷新后重试")


def _assessment_values(assessment: Assessment) -> tuple[object, ...]:
    return (
        assessment.assessment_id,
        assessment.case_id,
        assessment.version,
        assessment.status,
        assessment.assessment_date.isoformat(),
        assessment.jurisdiction,
        assessment.ruleset_version,
        json.dumps(assessment.fact_versions, ensure_ascii=False),
        json.dumps(
            [item.model_dump(mode="json") for item in assessment.policy_evaluations],
            ensure_ascii=False,
        ),
        assessment.risk_level,
        json.dumps(assessment.candidate_paths, ensure_ascii=False),
        assessment.generated_by_run_id,
        assessment.approved_by,
        assessment.approved_at,
        assessment.review_comment,
        assessment.created_at,
        assessment.updated_at,
    )


def _finding_values(finding: Finding) -> tuple[object, ...]:
    return (
        finding.finding_id,
        finding.assessment_id,
        finding.finding_type,
        finding.severity,
        finding.title,
        finding.description,
        json.dumps(finding.fact_ids, ensure_ascii=False),
        json.dumps(finding.evidence_ids, ensure_ascii=False),
        json.dumps(finding.clause_ids, ensure_ascii=False),
        json.dumps(finding.rule_ids, ensure_ascii=False),
        finding.status,
    )


def _evidence_citation_values(
    citation: AssessmentEvidenceCitation,
) -> tuple[object, ...]:
    return (
        citation.citation_id,
        citation.assessment_id,
        citation.source_evidence_id,
        citation.fact_id,
        citation.fact_version,
        citation.document_id,
        citation.document_version_id,
        citation.page_number,
        citation.quote,
        citation.start_offset,
        citation.end_offset,
        citation.source_sha256,
        citation.created_at,
    )


def _action_values(action: ActionItem) -> tuple[object, ...]:
    return (
        action.action_id,
        action.assessment_id,
        action.title,
        action.description,
        action.priority,
        action.owner_id,
        action.due_at,
        action.status,
        json.dumps(action.related_finding_ids, ensure_ascii=False),
    )


def _update_assessment_status(conn: Any, assessment: Assessment) -> None:
    conn.execute(
        """
        UPDATE assessments SET
            status = ?, approved_by = ?, approved_at = ?,
            review_comment = ?, updated_at = ?
        WHERE assessment_id = ?
        """,
        (
            assessment.status,
            assessment.approved_by,
            assessment.approved_at,
            assessment.review_comment,
            assessment.updated_at,
            assessment.assessment_id,
        ),
    )


def _row_to_assessment(row: Any) -> Assessment:
    return Assessment(
        assessment_id=row["assessment_id"],
        case_id=row["case_id"],
        version=row["version"],
        status=_validate_assessment_status(row["status"]),
        assessment_date=date.fromisoformat(row["assessment_date"]),
        jurisdiction=row["jurisdiction"],
        ruleset_version=row["ruleset_version"],
        fact_versions=json.loads(row["fact_versions_json"]),
        policy_evaluations=[
            PolicyEvaluation.model_validate(item)
            for item in json.loads(row["policy_evaluations_json"])
        ],
        risk_level=_validate_risk_level(row["risk_level"]),
        candidate_paths=json.loads(row["candidate_paths_json"]),
        generated_by_run_id=row["generated_by_run_id"],
        approved_by=row["approved_by"],
        approved_at=row["approved_at"],
        review_comment=row["review_comment"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_finding(row: Any) -> Finding:
    return Finding(
        finding_id=row["finding_id"],
        assessment_id=row["assessment_id"],
        finding_type=_validate_finding_type(row["finding_type"]),
        severity=_validate_finding_severity(row["severity"]),
        title=row["title"],
        description=row["description"],
        fact_ids=json.loads(row["fact_ids_json"]),
        evidence_ids=json.loads(row["evidence_ids_json"]),
        clause_ids=json.loads(row["clause_ids_json"]),
        rule_ids=json.loads(row["rule_ids_json"]),
        status=_validate_finding_status(row["status"]),
    )


def _row_to_evidence_citation(row: Any) -> AssessmentEvidenceCitation:
    return AssessmentEvidenceCitation(
        citation_id=row["citation_id"],
        assessment_id=row["assessment_id"],
        source_evidence_id=row["source_evidence_id"],
        fact_id=row["fact_id"],
        fact_version=row["fact_version"],
        document_id=row["document_id"],
        document_version_id=row["document_version_id"],
        page_number=row["page_number"],
        quote=row["quote"],
        start_offset=row["start_offset"],
        end_offset=row["end_offset"],
        source_sha256=row["source_sha256"],
        created_at=row["created_at"],
    )


def _row_to_action(row: Any) -> ActionItem:
    return ActionItem(
        action_id=row["action_id"],
        assessment_id=row["assessment_id"],
        title=row["title"],
        description=row["description"],
        priority=_validate_action_priority(row["priority"]),
        owner_id=row["owner_id"],
        due_at=row["due_at"],
        status=_validate_action_status(row["status"]),
        related_finding_ids=json.loads(row["related_finding_ids_json"]),
    )


def _validate_assessment_status(value: str) -> AssessmentStatus:
    valid = {"draft", "review_required", "approved", "rejected", "superseded"}
    if value not in valid:
        raise ValueError(f"invalid assessment status in DB: {value!r}")
    return cast("AssessmentStatus", value)


def _validate_risk_level(value: str) -> RiskLevel:
    if value not in {"low", "medium", "high", "critical", "unknown"}:
        raise ValueError(f"invalid risk level in DB: {value!r}")
    return cast("RiskLevel", value)


def _validate_finding_type(value: str) -> FindingType:
    valid = {
        "risk",
        "missing_fact",
        "missing_material",
        "evidence_conflict",
        "rule_trigger",
        "recommendation",
    }
    if value not in valid:
        raise ValueError(f"invalid finding type in DB: {value!r}")
    return cast("FindingType", value)


def _validate_finding_severity(value: str) -> FindingSeverity:
    if value not in {"info", "low", "medium", "high", "critical"}:
        raise ValueError(f"invalid finding severity in DB: {value!r}")
    return cast("FindingSeverity", value)


def _validate_finding_status(value: str) -> FindingStatus:
    if value not in {"open", "accepted", "resolved", "dismissed"}:
        raise ValueError(f"invalid finding status in DB: {value!r}")
    return cast("FindingStatus", value)


def _validate_action_priority(value: str) -> ActionPriority:
    if value not in {"low", "medium", "high", "urgent"}:
        raise ValueError(f"invalid action priority in DB: {value!r}")
    return cast("ActionPriority", value)


def _validate_action_status(value: str) -> ActionStatus:
    if value not in {"todo", "in_progress", "done", "cancelled"}:
        raise ValueError(f"invalid action status in DB: {value!r}")
    return cast("ActionStatus", value)
