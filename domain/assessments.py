"""V2 Assessment、Finding、ActionItem 与审批状态模型。"""

from __future__ import annotations

import time
from datetime import date
from typing import ClassVar, Literal, cast

from pydantic import Field, model_validator

from domain.errors import InvalidAssessmentTransition
from domain.models import BaseDomainModel
from domain.policies import PolicyEvaluation

AssessmentStatus = Literal[
    "draft",
    "review_required",
    "approved",
    "rejected",
    "superseded",
]
RiskLevel = Literal["low", "medium", "high", "critical", "unknown"]
FindingType = Literal[
    "risk",
    "missing_fact",
    "missing_material",
    "evidence_conflict",
    "rule_trigger",
    "recommendation",
]
FindingSeverity = Literal["info", "low", "medium", "high", "critical"]
FindingStatus = Literal["open", "accepted", "resolved", "dismissed"]
ActionPriority = Literal["low", "medium", "high", "urgent"]
ActionStatus = Literal["todo", "in_progress", "done", "cancelled"]


class AssessmentEvidenceCitation(BaseDomainModel):
    """Assessment 生成时冻结的 Fact 原文引用。"""

    citation_id: str = Field(min_length=1)
    assessment_id: str = Field(min_length=1)
    source_evidence_id: str = Field(min_length=1)
    fact_id: str = Field(min_length=1)
    fact_version: int = Field(ge=1)
    document_id: str = Field(min_length=1)
    document_version_id: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=4000)
    start_offset: int | None = Field(default=None, ge=0)
    end_offset: int | None = Field(default=None, ge=0)
    source_sha256: str = Field(min_length=64, max_length=64)
    created_at: float

    @model_validator(mode="after")
    def validate_citation(self) -> AssessmentEvidenceCitation:
        if not self.quote.strip():
            raise ValueError("Assessment Evidence quote 不能为空白字符串")
        if (self.start_offset is None) != (self.end_offset is None):
            raise ValueError("start_offset 和 end_offset 必须同时为空或同时存在")
        if (
            self.start_offset is not None
            and self.end_offset is not None
            and self.end_offset <= self.start_offset
        ):
            raise ValueError("end_offset 必须大于 start_offset")
        if len(self.source_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.source_sha256
        ):
            raise ValueError("source_sha256 必须是 64 位小写十六进制")
        return self


class Finding(BaseDomainModel):
    finding_id: str = Field(min_length=1)
    assessment_id: str = Field(min_length=1)
    finding_type: FindingType
    severity: FindingSeverity
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=5000)
    fact_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    clause_ids: list[str] = Field(default_factory=list)
    rule_ids: list[str] = Field(default_factory=list)
    status: FindingStatus = "open"

    @model_validator(mode="after")
    def validate_finding(self) -> Finding:
        if not self.title.strip():
            raise ValueError("title 不能为空白字符串")
        for field_name in ("fact_ids", "evidence_ids", "clause_ids", "rule_ids"):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} 不能包含重复值")
        return self


class ActionItem(BaseDomainModel):
    action_id: str = Field(min_length=1)
    assessment_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=5000)
    priority: ActionPriority
    owner_id: str | None = None
    due_at: float | None = None
    status: ActionStatus = "todo"
    related_finding_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_action(self) -> ActionItem:
        if not self.title.strip():
            raise ValueError("title 不能为空白字符串")
        if len(self.related_finding_ids) != len(set(self.related_finding_ids)):
            raise ValueError("related_finding_ids 不能重复")
        return self


class Assessment(BaseDomainModel):
    """一次不可变事实/规则快照对应的评估版本。"""

    _ALLOWED_TRANSITIONS: ClassVar[dict[str, frozenset[str]]] = {
        "draft": frozenset({"review_required", "superseded"}),
        "review_required": frozenset({"approved", "rejected", "superseded"}),
        "approved": frozenset({"superseded"}),
        "rejected": frozenset({"superseded"}),
        "superseded": frozenset(),
    }

    assessment_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    status: AssessmentStatus = "draft"
    assessment_date: date
    jurisdiction: str = Field(min_length=1, max_length=32)
    ruleset_version: str = Field(min_length=1, max_length=100)
    fact_versions: dict[str, int] = Field(default_factory=dict)
    policy_evaluations: list[PolicyEvaluation] = Field(default_factory=list)
    risk_level: RiskLevel = "unknown"
    candidate_paths: list[str] = Field(default_factory=list)
    generated_by_run_id: str | None = None
    approved_by: str | None = None
    approved_at: float | None = None
    review_comment: str = Field(default="", max_length=5000)
    created_at: float
    updated_at: float

    @model_validator(mode="after")
    def validate_assessment(self) -> Assessment:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at 不能早于 created_at")
        if any(version < 1 for version in self.fact_versions.values()):
            raise ValueError("fact_versions 必须为正整数")
        if len(self.candidate_paths) != len(set(self.candidate_paths)):
            raise ValueError("candidate_paths 不能重复")
        if (self.approved_by is None) != (self.approved_at is None):
            raise ValueError("approved_by 和 approved_at 必须同时为空或同时存在")
        if self.status == "approved" and self.approved_by is None:
            raise ValueError("approved Assessment 必须记录审批人和审批时间")
        if self.status != "approved" and self.approved_by is not None:
            raise ValueError("非 approved Assessment 不能保留审批信息")
        return self

    def transition_to(
        self,
        target: AssessmentStatus,
        *,
        actor_id: str,
        comment: str = "",
        at: float | None = None,
    ) -> Assessment:
        if target == self.status:
            return self
        if target not in self._ALLOWED_TRANSITIONS[self.status]:
            raise InvalidAssessmentTransition(
                self.assessment_id,
                self.status,
                target,
            )
        transition_time = time.time() if at is None else at
        if transition_time < self.updated_at:
            raise ValueError("Assessment 状态时间不能早于更新时间")
        approval = (
            {"approved_by": actor_id, "approved_at": transition_time}
            if target == "approved"
            else {"approved_by": None, "approved_at": None}
        )
        return cast(
            "Assessment",
            self.model_copy(
                update={
                    "status": target,
                    "review_comment": comment,
                    "updated_at": transition_time,
                    **approval,
                }
            ),
        )


class AssessmentBundle(BaseDomainModel):
    """Assessment 及其风险项与整改行动。"""

    assessment: Assessment
    findings: list[Finding] = Field(default_factory=list)
    action_items: list[ActionItem] = Field(default_factory=list)
    evidence_citations: list[AssessmentEvidenceCitation] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_bundle(self) -> AssessmentBundle:
        assessment_id = self.assessment.assessment_id
        if any(finding.assessment_id != assessment_id for finding in self.findings):
            raise ValueError("Finding 必须属于当前 Assessment")
        if any(action.assessment_id != assessment_id for action in self.action_items):
            raise ValueError("ActionItem 必须属于当前 Assessment")
        if any(citation.assessment_id != assessment_id for citation in self.evidence_citations):
            raise ValueError("EvidenceCitation 必须属于当前 Assessment")
        citation_ids = [citation.citation_id for citation in self.evidence_citations]
        if len(citation_ids) != len(set(citation_ids)):
            raise ValueError("EvidenceCitation citation_id 不能重复")
        source_evidence_ids = [citation.source_evidence_id for citation in self.evidence_citations]
        if len(source_evidence_ids) != len(set(source_evidence_ids)):
            raise ValueError("EvidenceCitation source_evidence_id 不能重复")
        citations_by_id = {citation.citation_id: citation for citation in self.evidence_citations}
        referenced_citation_ids: set[str] = set()
        for finding in self.findings:
            missing = set(finding.evidence_ids) - set(citations_by_id)
            if missing:
                raise ValueError("Finding 引用了不存在的 EvidenceCitation")
            referenced_citation_ids.update(finding.evidence_ids)
            if any(
                citations_by_id[evidence_id].fact_id not in finding.fact_ids
                for evidence_id in finding.evidence_ids
            ):
                raise ValueError("Finding EvidenceCitation 必须属于其 fact_ids")
        if set(citations_by_id) != referenced_citation_ids:
            raise ValueError("EvidenceCitation 必须且只能由 Finding 引用")
        finding_ids = {finding.finding_id for finding in self.findings}
        for action in self.action_items:
            missing = set(action.related_finding_ids) - finding_ids
            if missing:
                raise ValueError("ActionItem 引用了不存在的 Finding")
        return self
