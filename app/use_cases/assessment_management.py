"""V2 Assessment 确定性生成、版本化与查询用例。"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, cast

from domain.assessments import (
    ActionItem,
    Assessment,
    AssessmentBundle,
    Finding,
    FindingSeverity,
    RiskLevel,
)
from domain.errors import AssessmentNotFound
from domain.workspaces import WorkspaceRole

if TYPE_CHECKING:
    from app.use_cases.case_management import CaseManagementUseCase
    from app.use_cases.policy_management import PolicyManagementUseCase
    from app.use_cases.workspace_management import WorkspaceManagementUseCase
    from domain.facts import CaseFact
    from domain.policies import PolicyEvaluation
    from domain.ports import AssessmentRepoPort, CaseFactRepoPort

_WRITE_ROLES: set[WorkspaceRole] = {"editor", "reviewer", "admin"}
_RISK_ORDER: dict[str, int] = {
    "unknown": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class AssessmentManagementUseCase:
    def __init__(
        self,
        *,
        assessment_repo: AssessmentRepoPort,
        fact_repo: CaseFactRepoPort,
        case_management: CaseManagementUseCase,
        workspace_management: WorkspaceManagementUseCase,
        policy_management: PolicyManagementUseCase,
    ) -> None:
        self._assessments = assessment_repo
        self._facts = fact_repo
        self._case_management = case_management
        self._workspace_management = workspace_management
        self._policies = policy_management

    def generate(
        self,
        case_id: str,
        actor_id: str,
        *,
        ruleset_version: str,
        generated_by_run_id: str | None = None,
    ) -> AssessmentBundle:
        case = self._case_management.get_case(case_id, actor_id)
        self._workspace_management.require_role(
            case.workspace_id,
            actor_id,
            _WRITE_ROLES,
            action="生成 Assessment",
        )
        if case.assessment_date is None:
            raise ValueError("案件必须设置 assessment_date 才能生成 Assessment")

        confirmed_facts = self._facts.list_for_case(
            case.case_id,
            statuses={"confirmed"},
        )
        report = self._policies.evaluate_case(
            case.case_id,
            actor_id,
            ruleset_version=ruleset_version,
        )
        fact_versions = _fact_versions(confirmed_facts)
        now = time.time()
        assessment_id = _new_id("assessment")
        findings, actions = _build_findings_and_actions(
            assessment_id,
            report.evaluations,
            confirmed_facts,
        )
        assessment = Assessment(
            assessment_id=assessment_id,
            case_id=case.case_id,
            version=self._assessments.next_version(case.case_id),
            status="review_required",
            assessment_date=case.assessment_date,
            jurisdiction=case.jurisdiction,
            ruleset_version=ruleset_version,
            fact_versions=fact_versions,
            policy_evaluations=list(report.evaluations),
            risk_level=_aggregate_risk_level(report.evaluations),
            candidate_paths=_candidate_paths(report.evaluations),
            generated_by_run_id=generated_by_run_id,
            created_at=now,
            updated_at=now,
        )
        bundle = AssessmentBundle(
            assessment=assessment,
            findings=findings,
            action_items=actions,
        )

        previous_bundle = self._assessments.get_active(case.case_id)
        previous = None
        if previous_bundle is not None:
            previous = previous_bundle.assessment.transition_to(
                "superseded",
                actor_id=actor_id,
                comment=f"由 Assessment v{assessment.version} 替代",
                at=max(now, previous_bundle.assessment.updated_at),
            )
        updated_case = case.model_copy(
            update={
                "active_assessment_id": assessment.assessment_id,
                "updated_at": max(now, case.updated_at),
            }
        )
        self._assessments.create_version(bundle, previous, updated_case)
        return bundle

    def get(self, assessment_id: str, actor_id: str) -> AssessmentBundle:
        bundle = self._assessments.get(assessment_id)
        if bundle is None:
            raise AssessmentNotFound(assessment_id)
        self._case_management.get_case(bundle.assessment.case_id, actor_id)
        return bundle

    def get_active(self, case_id: str, actor_id: str) -> AssessmentBundle | None:
        self._case_management.get_case(case_id, actor_id)
        return self._assessments.get_active(case_id)

    def list_versions(self, case_id: str, actor_id: str) -> list[Assessment]:
        self._case_management.get_case(case_id, actor_id)
        return cast("list[Assessment]", self._assessments.list_for_case(case_id))


def _fact_versions(facts: list[CaseFact]) -> dict[str, int]:
    field_names = [fact.field_name for fact in facts]
    duplicates = sorted(
        field_name for field_name in set(field_names) if field_names.count(field_name) > 1
    )
    if duplicates:
        raise ValueError(f"同一字段存在多个 confirmed 事实: {', '.join(duplicates)}")
    return {fact.field_name: fact.version for fact in facts}


def _build_findings_and_actions(
    assessment_id: str,
    evaluations: list[PolicyEvaluation],
    facts: list[CaseFact],
) -> tuple[list[Finding], list[ActionItem]]:
    facts_by_field = {fact.field_name: fact for fact in facts}
    findings: list[Finding] = []
    actions: list[ActionItem] = []
    for evaluation in evaluations:
        if evaluation.status == "triggered":
            risk_level = _normalize_risk_level(evaluation.result.get("risk_level"))
            finding = Finding(
                finding_id=_new_id("finding"),
                assessment_id=assessment_id,
                finding_type="rule_trigger",
                severity=_finding_severity(risk_level),
                title=f"规则 {evaluation.rule_id} 已触发",
                description=str(evaluation.result.get("description") or ""),
                fact_ids=[
                    facts_by_field[field].fact_id
                    for field in evaluation.consumed_fact_versions
                    if field in facts_by_field
                ],
                clause_ids=list(evaluation.source_clause_ids),
                rule_ids=[evaluation.rule_id],
            )
            findings.append(finding)
            actions.extend(_actions_from_result(assessment_id, finding, evaluation))
        elif evaluation.status == "missing_facts":
            for field_name in evaluation.missing_fact_fields:
                finding = Finding(
                    finding_id=_new_id("finding"),
                    assessment_id=assessment_id,
                    finding_type="missing_fact",
                    severity="high",
                    title=f"缺少必要事实：{field_name}",
                    rule_ids=[evaluation.rule_id],
                    clause_ids=list(evaluation.source_clause_ids),
                )
                findings.append(finding)
                actions.append(
                    ActionItem(
                        action_id=_new_id("action"),
                        assessment_id=assessment_id,
                        title=f"确认事实：{field_name}",
                        priority="high",
                        related_finding_ids=[finding.finding_id],
                    )
                )
    return findings, actions


def _actions_from_result(
    assessment_id: str,
    finding: Finding,
    evaluation: PolicyEvaluation,
) -> list[ActionItem]:
    actions: list[ActionItem] = []
    for title in _string_list(evaluation.result.get("required_actions")):
        actions.append(
            ActionItem(
                action_id=_new_id("action"),
                assessment_id=assessment_id,
                title=title,
                priority="high",
                related_finding_ids=[finding.finding_id],
            )
        )
    for material in _string_list(evaluation.result.get("required_materials")):
        actions.append(
            ActionItem(
                action_id=_new_id("action"),
                assessment_id=assessment_id,
                title=f"补充材料：{material}",
                priority="high",
                related_finding_ids=[finding.finding_id],
            )
        )
    return actions


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError("规则结果中的行动或材料必须是非空字符串数组")
    return list(value)


def _candidate_paths(evaluations: list[PolicyEvaluation]) -> list[str]:
    paths: list[str] = []
    for evaluation in evaluations:
        if evaluation.status != "triggered":
            continue
        value = evaluation.result.get("candidate_path")
        candidates = [value] if isinstance(value, str) else _string_list(value)
        for candidate in candidates:
            if candidate and candidate not in paths:
                paths.append(candidate)
    return paths


def _aggregate_risk_level(evaluations: list[PolicyEvaluation]) -> RiskLevel:
    levels = [
        _normalize_risk_level(evaluation.result.get("risk_level"))
        for evaluation in evaluations
        if evaluation.status == "triggered"
    ]
    if not levels:
        return (
            "unknown"
            if any(evaluation.status == "missing_facts" for evaluation in evaluations)
            else "low"
        )
    return max(levels, key=lambda level: _RISK_ORDER[level])


def _normalize_risk_level(value: object) -> RiskLevel:
    if value is None:
        return "medium"
    if not isinstance(value, str) or value not in _RISK_ORDER:
        raise ValueError(f"规则结果包含非法 risk_level: {value!r}")
    return cast("RiskLevel", value)


def _finding_severity(risk_level: RiskLevel) -> FindingSeverity:
    if risk_level == "unknown":
        return "info"
    return risk_level
