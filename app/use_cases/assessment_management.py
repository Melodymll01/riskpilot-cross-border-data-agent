"""V2 Assessment 确定性生成、版本化与查询用例。"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, cast

from domain.assessments import (
    ActionItem,
    Assessment,
    AssessmentBundle,
    AssessmentEvidenceCitation,
    AssessmentStatus,
    Finding,
    FindingSeverity,
    RiskLevel,
)
from domain.cases import CaseStatus
from domain.errors import (
    AssessmentNotActive,
    AssessmentNotFound,
    CaseNotFound,
    InvalidDocumentContent,
    WorkspaceAccessDenied,
)
from domain.workspaces import WorkspaceRole

if TYPE_CHECKING:
    from app.use_cases.case_management import CaseManagementUseCase
    from app.use_cases.policy_management import PolicyManagementUseCase
    from app.use_cases.workspace_management import WorkspaceManagementUseCase
    from domain.facts import CaseFact, CaseFactEvidence
    from domain.policies import PolicyEvaluation
    from domain.ports import AssessmentRepoPort, CaseFactRepoPort, DocumentRepoPort

_WRITE_ROLES: set[WorkspaceRole] = {"editor", "reviewer", "admin"}
_REVIEW_ROLES: set[WorkspaceRole] = {"reviewer", "admin"}
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
        document_repo: DocumentRepoPort,
        case_management: CaseManagementUseCase,
        workspace_management: WorkspaceManagementUseCase,
        policy_management: PolicyManagementUseCase,
    ) -> None:
        self._assessments = assessment_repo
        self._facts = fact_repo
        self._documents = document_repo
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
        if case.status not in {
            "ready_for_assessment",
            "assessing",
            "review_required",
        }:
            raise ValueError(
                "案件必须处于 ready_for_assessment、assessing 或 review_required "
                "才能生成 Assessment"
            )
        published_rules = self._policies.list_rules(
            case.workspace_id,
            actor_id,
            ruleset_version=ruleset_version,
            jurisdiction=case.jurisdiction,
            status="published",
        )
        if not published_rules:
            raise ValueError(f"规则集 {ruleset_version!r} 在当前 Workspace 和法域下没有已发布规则")

        confirmed_facts = self._facts.list_for_case(
            case.case_id,
            statuses={"confirmed"},
        )
        report = self._policies.evaluate_case(
            case.case_id,
            actor_id,
            ruleset_version=ruleset_version,
        )
        if not report.evaluations:
            raise ValueError(
                f"规则集 {ruleset_version!r} 在评估日期 {case.assessment_date.isoformat()} "
                "没有生效规则"
            )
        fact_versions = _fact_versions(confirmed_facts)
        now = time.time()
        assessment_id = _new_id("assessment")
        finding_fact_fields = {
            field
            for evaluation in report.evaluations
            if evaluation.status == "triggered"
            for field in evaluation.consumed_fact_versions
        }
        finding_facts = [fact for fact in confirmed_facts if fact.field_name in finding_fact_fields]
        evidence_citations, evidence_ids_by_fact = self._snapshot_fact_evidence(
            assessment_id,
            finding_facts,
            created_at=now,
        )
        findings, actions = _build_findings_and_actions(
            assessment_id,
            report.evaluations,
            confirmed_facts,
            evidence_ids_by_fact,
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
            evidence_citations=evidence_citations,
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
        assessing_case = (
            case if case.status == "assessing" else case.transition_to("assessing", at=now)
        )
        review_case = assessing_case.transition_to("review_required", at=now)
        updated_case = review_case.model_copy(
            update={
                "active_assessment_id": assessment.assessment_id,
            }
        )
        self._assessments.create_version(bundle, previous, updated_case)
        return bundle

    def get(self, assessment_id: str, actor_id: str) -> AssessmentBundle:
        bundle: AssessmentBundle | None = self._assessments.get(assessment_id)
        if bundle is None:
            raise AssessmentNotFound(assessment_id)
        try:
            self._case_management.get_case(bundle.assessment.case_id, actor_id)
        except CaseNotFound as exc:
            raise AssessmentNotFound(assessment_id) from exc
        return bundle

    def get_active(self, case_id: str, actor_id: str) -> AssessmentBundle | None:
        self._case_management.get_case(case_id, actor_id)
        bundle: AssessmentBundle | None = self._assessments.get_active(case_id)
        return bundle

    def list_versions(self, case_id: str, actor_id: str) -> list[Assessment]:
        self._case_management.get_case(case_id, actor_id)
        assessments: list[Assessment] = self._assessments.list_for_case(case_id)
        return assessments

    def verify_references(
        self,
        assessment_id: str,
        actor_id: str,
    ) -> AssessmentBundle:
        bundle = self.get(assessment_id, actor_id)
        self._validate_assessment_references(bundle)
        return bundle

    def review(
        self,
        assessment_id: str,
        actor_id: str,
        *,
        decision: AssessmentStatus,
        comment: str = "",
    ) -> AssessmentBundle:
        if decision not in {"approved", "rejected"}:
            raise ValueError("Assessment 审批结果只能是 approved 或 rejected")
        if decision == "rejected" and not comment.strip():
            raise ValueError("拒绝 Assessment 时必须填写 review_comment")

        bundle: AssessmentBundle | None = self._assessments.get(assessment_id)
        if bundle is None:
            raise AssessmentNotFound(assessment_id)
        try:
            case = self._case_management.get_case(bundle.assessment.case_id, actor_id)
        except CaseNotFound as exc:
            raise AssessmentNotFound(assessment_id) from exc
        membership = self._workspace_management.require_role(
            case.workspace_id,
            actor_id,
            _REVIEW_ROLES,
            action=f"将 Assessment 审批为 {decision}",
        )
        if (
            case.reviewer_id is not None
            and actor_id != case.reviewer_id
            and membership.role != "admin"
        ):
            raise WorkspaceAccessDenied(
                case.workspace_id,
                actor_id,
                "审批分配给其他 Reviewer 的 Assessment",
            )
        if case.active_assessment_id != assessment_id:
            raise AssessmentNotActive(
                assessment_id,
                case.case_id,
                case.active_assessment_id,
            )
        if decision == "approved" and any(
            evaluation.status == "missing_facts"
            for evaluation in bundle.assessment.policy_evaluations
        ):
            raise ValueError("Assessment 仍存在缺失事实，不能批准")
        if decision == "approved":
            self._validate_assessment_references(bundle)

        updated_assessment = bundle.assessment.transition_to(
            decision,
            actor_id=actor_id,
            comment=comment,
        )
        if updated_assessment is bundle.assessment:
            return bundle
        target_case_status: CaseStatus = (
            "completed" if decision == "approved" else "ready_for_assessment"
        )
        updated_case = case.transition_to(
            target_case_status,
            at=updated_assessment.updated_at,
        )
        self._assessments.save_review(updated_assessment, updated_case)
        return AssessmentBundle(
            assessment=updated_assessment,
            findings=bundle.findings,
            action_items=bundle.action_items,
            evidence_citations=bundle.evidence_citations,
        )

    def _snapshot_fact_evidence(
        self,
        assessment_id: str,
        facts: list[CaseFact],
        *,
        created_at: float,
    ) -> tuple[list[AssessmentEvidenceCitation], dict[str, list[str]]]:
        citations: list[AssessmentEvidenceCitation] = []
        evidence_ids_by_fact: dict[str, list[str]] = {}
        for fact in facts:
            fact_evidence = self._facts.list_evidence(
                fact.fact_id,
                fact_version=fact.version,
            )
            if fact.source_type == "document" and not fact_evidence:
                raise InvalidDocumentContent(f"document 事实 {fact.fact_id} 缺少当前版本证据")
            for evidence in fact_evidence:
                self._validate_fact_evidence(fact, evidence)
                version = self._documents.get_version(evidence.document_version_id)
                assert version is not None
                citation = AssessmentEvidenceCitation(
                    citation_id=_new_id("assessment_evidence"),
                    assessment_id=assessment_id,
                    source_evidence_id=evidence.evidence_id,
                    fact_id=fact.fact_id,
                    fact_version=fact.version,
                    document_id=evidence.document_id,
                    document_version_id=evidence.document_version_id,
                    page_number=evidence.page_number,
                    quote=evidence.quote,
                    start_offset=evidence.start_offset,
                    end_offset=evidence.end_offset,
                    source_sha256=version.sha256,
                    created_at=created_at,
                )
                citations.append(citation)
                evidence_ids_by_fact.setdefault(fact.fact_id, []).append(citation.citation_id)
        return citations, evidence_ids_by_fact

    def _validate_assessment_references(self, bundle: AssessmentBundle) -> None:
        evaluations_by_rule = {
            evaluation.rule_id: evaluation for evaluation in bundle.assessment.policy_evaluations
        }
        citations_by_fact: dict[str, set[str]] = {}
        for citation in bundle.evidence_citations:
            citations_by_fact.setdefault(citation.fact_id, set()).add(citation.citation_id)
        for finding in bundle.findings:
            if finding.finding_type == "rule_trigger":
                if len(finding.rule_ids) != 1:
                    raise InvalidDocumentContent("rule_trigger Finding 必须且只能引用一个规则")
                evaluation = evaluations_by_rule.get(finding.rule_ids[0])
                if evaluation is None or evaluation.status != "triggered":
                    raise InvalidDocumentContent(
                        "rule_trigger Finding 引用了不存在或未触发的规则快照"
                    )
                if set(finding.clause_ids) != set(evaluation.source_clause_ids):
                    raise InvalidDocumentContent(
                        "Finding clause_ids 与 PolicyEvaluation 快照不一致"
                    )
            for fact_id in finding.fact_ids:
                fact = self._facts.get(fact_id)
                if fact is None or fact.status != "confirmed":
                    raise InvalidDocumentContent(
                        f"Finding 引用的 Fact {fact_id} 已不存在或不再 confirmed"
                    )
                if bundle.assessment.fact_versions.get(fact.field_name) != fact.version:
                    raise InvalidDocumentContent(f"Finding 引用的 Fact {fact_id} 版本已漂移")
                expected_evidence_ids = citations_by_fact.get(fact_id, set())
                if fact.source_type == "document" and not expected_evidence_ids:
                    raise InvalidDocumentContent(
                        f"Finding 引用的 document Fact {fact_id} 缺少证据快照"
                    )
                if not expected_evidence_ids.issubset(finding.evidence_ids):
                    raise InvalidDocumentContent(f"Finding 未完整引用 Fact {fact_id} 的证据快照")
        for citation in bundle.evidence_citations:
            fact = self._facts.get(citation.fact_id)
            if fact is None or fact.status != "confirmed" or fact.version != citation.fact_version:
                raise InvalidDocumentContent(
                    f"Assessment Evidence {citation.citation_id} 的 Fact 版本已漂移"
                )
            source_evidence = next(
                (
                    evidence
                    for evidence in self._facts.list_evidence(
                        citation.fact_id,
                        fact_version=citation.fact_version,
                    )
                    if evidence.evidence_id == citation.source_evidence_id
                ),
                None,
            )
            if source_evidence is None or (
                source_evidence.document_id,
                source_evidence.document_version_id,
                source_evidence.page_number,
                source_evidence.quote,
                source_evidence.start_offset,
                source_evidence.end_offset,
            ) != (
                citation.document_id,
                citation.document_version_id,
                citation.page_number,
                citation.quote,
                citation.start_offset,
                citation.end_offset,
            ):
                raise InvalidDocumentContent(
                    f"Assessment Evidence {citation.citation_id} 与原 Fact Evidence 不一致"
                )
            self._validate_citation_source(citation, case_id=fact.case_id)

    def _validate_fact_evidence(
        self,
        fact: CaseFact,
        evidence: CaseFactEvidence,
    ) -> None:
        if (
            evidence.fact_id != fact.fact_id
            or evidence.fact_version != fact.version
            or evidence.case_id != fact.case_id
        ):
            raise InvalidDocumentContent("Fact Evidence 与当前 confirmed Fact 版本不一致")
        self._validate_document_quote(
            case_id=fact.case_id,
            document_id=evidence.document_id,
            document_version_id=evidence.document_version_id,
            page_number=evidence.page_number,
            quote=evidence.quote,
            start_offset=evidence.start_offset,
            end_offset=evidence.end_offset,
            expected_sha256=None,
        )

    def _validate_citation_source(
        self,
        citation: AssessmentEvidenceCitation,
        *,
        case_id: str,
    ) -> None:
        self._validate_document_quote(
            case_id=case_id,
            document_id=citation.document_id,
            document_version_id=citation.document_version_id,
            page_number=citation.page_number,
            quote=citation.quote,
            start_offset=citation.start_offset,
            end_offset=citation.end_offset,
            expected_sha256=citation.source_sha256,
        )

    def _validate_document_quote(
        self,
        *,
        case_id: str,
        document_id: str,
        document_version_id: str,
        page_number: int,
        quote: str,
        start_offset: int | None,
        end_offset: int | None,
        expected_sha256: str | None,
    ) -> None:
        binding = self._documents.get_binding(case_id, document_id)
        document = self._documents.get(document_id)
        version = self._documents.get_version(document_version_id)
        snapshot = self._documents.get_parse_snapshot(document_version_id)
        if (
            binding is None
            or document is None
            or version is None
            or snapshot is None
            or version.document_id != document_id
            or document.status != "ready"
            or document.current_version_id != document_version_id
            or version.sha256 != snapshot.source_sha256
            or (expected_sha256 is not None and version.sha256 != expected_sha256)
            or page_number > snapshot.page_count
        ):
            raise InvalidDocumentContent("Assessment Evidence 引用的文档版本已失效")
        page = snapshot.pages[page_number - 1]
        parts = [page.text]
        parts.extend(table.markdown for table in page.tables)
        page_body = "\n\n".join(part for part in parts if part)
        if start_offset is not None and end_offset is not None:
            if page_body[start_offset:end_offset] != quote:
                raise InvalidDocumentContent("Assessment Evidence offset 与原文不一致")
        elif quote not in page_body:
            raise InvalidDocumentContent("Assessment Evidence quote 未在当前原文页中找到")


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
    evidence_ids_by_fact: dict[str, list[str]],
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
                evidence_ids=[
                    evidence_id
                    for field in evaluation.consumed_fact_versions
                    if field in facts_by_field
                    for evidence_id in evidence_ids_by_fact.get(
                        facts_by_field[field].fact_id,
                        [],
                    )
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
