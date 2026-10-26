"""Evidence-backed preliminary risk analysis, independent of enterprise workflows."""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import Field, model_validator

from domain.models import BaseDomainModel
from domain.qa import EvidenceQACitation


class RiskFinding(BaseDomainModel):
    finding_id: str = Field(min_length=1, max_length=60, pattern=r"^[A-Za-z0-9_-]+$")
    text: str = Field(min_length=1, max_length=1100)
    severity: Literal["low", "medium", "high"]
    citation_ids: list[str] = Field(min_length=1, max_length=12)
    condition_message_ids: list[str] = Field(default_factory=list, max_length=12)
    recommendation: str = Field(default="", max_length=700)


class RiskDraft(BaseDomainModel):
    findings: list[RiskFinding] = Field(default_factory=list, max_length=8)
    missing_information: list[
        Literal[
            "主体类型",
            "数据类型",
            "涉及人数",
            "敏感个人信息",
            "重要数据情况",
            "境外接收方",
            "出境目的",
            "处理与留存措施",
            "适用法域与日期",
            "法规依据",
            "材料证据",
        ]
    ] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def unique_findings(self) -> RiskDraft:
        ids = [item.finding_id for item in self.findings]
        if len(ids) != len(set(ids)):
            raise ValueError("风险项 ID 必须唯一")
        return self


class RiskAssessment(BaseDomainModel):
    assessment_id: str
    owner_id: str
    run_id: str
    question: str
    status: Literal["assessed", "needs_information", "refused"]
    risk_level: Literal["low", "medium", "high", "unknown"]
    findings: list[RiskFinding] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    citations: list[EvidenceQACitation] = Field(default_factory=list)
    input_citation_ids: list[str] = Field(default_factory=list)
    created_at: float
    method: Literal["evidence_risk_review_v1"] = "evidence_risk_review_v1"


class RiskAssessmentRepoPort(Protocol):
    def save(self, assessment: RiskAssessment) -> None: ...
    def for_run(self, owner_id: str, run_id: str) -> RiskAssessment | None: ...
