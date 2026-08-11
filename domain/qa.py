"""V3 Evidence QA 范围、引用、Claim 与结构校验模型。"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, model_validator

from domain.models import BaseDomainModel

QACorpus = Literal["regulatory", "workspace", "case", "assessment"]
EvidenceQAStatus = Literal["answered", "partially_answered", "refused"]
ClaimRepairStatus = Literal["not_needed", "repaired", "failed"]
ClaimRemovalReason = Literal[
    "uncited",
    "unknown_citation",
    "unsupported",
    "verification_error",
]
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class EvidenceQAScope(BaseDomainModel):
    """由服务端鉴权结果构造的检索范围，不接受客户端注入归属字段。"""

    corpora: list[QACorpus] = Field(min_length=1)
    workspace_id: str | None = None
    case_id: str | None = None
    assessment_id: str | None = None

    @model_validator(mode="after")
    def validate_scope(self) -> EvidenceQAScope:
        if len(self.corpora) != len(set(self.corpora)):
            raise ValueError("corpora 不能重复")
        needs_workspace = any(
            corpus in {"workspace", "case", "assessment"} for corpus in self.corpora
        )
        needs_case = any(corpus in {"case", "assessment"} for corpus in self.corpora)
        if not needs_workspace and self.workspace_id is not None:
            raise ValueError("当前 corpora 不接受 workspace_id")
        if not needs_case and self.case_id is not None:
            raise ValueError("当前 corpora 不接受 case_id")
        if "assessment" not in self.corpora and self.assessment_id is not None:
            raise ValueError("当前 corpora 不接受 assessment_id")
        if needs_workspace and not self.workspace_id:
            raise ValueError("Workspace、Case 或 Assessment 范围必须提供 workspace_id")
        if needs_case and not self.case_id:
            raise ValueError("Case 或 Assessment 范围必须提供 case_id")
        if "assessment" in self.corpora and not self.assessment_id:
            raise ValueError("Assessment 范围必须提供 assessment_id")
        if self.case_id is not None and self.workspace_id is None:
            raise ValueError("case_id 不能脱离 workspace_id")
        if self.assessment_id is not None and self.case_id is None:
            raise ValueError("assessment_id 不能脱离 case_id")
        return self


class EvidenceQACitation(BaseDomainModel):
    """回答引用的不可变证据定位。"""

    citation_id: str = Field(min_length=1, max_length=100)
    corpus: QACorpus
    source_id: str = Field(min_length=1, max_length=500)
    source_name: str = Field(min_length=1, max_length=500)
    title: str = Field(default="", max_length=500)
    quote: str = Field(min_length=1, max_length=4000)
    source_url: str | None = Field(default=None, max_length=2000)
    workspace_id: str | None = None
    case_id: str | None = None
    document_id: str | None = None
    document_version_id: str | None = None
    page_number: int | None = Field(default=None, ge=1)
    source_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    assessment_id: str | None = None
    clause_id: str | None = None
    score: float = Field(default=0.0, ge=0.0)

    @model_validator(mode="after")
    def validate_source_location(self) -> EvidenceQACitation:
        if not self.quote.strip():
            raise ValueError("quote 不能为空白字符串")
        if self.source_sha256 is not None and not _SHA256_RE.fullmatch(self.source_sha256):
            raise ValueError("source_sha256 必须是 64 位小写十六进制")
        if self.corpus in {"workspace", "case"}:
            required = {
                "workspace_id": self.workspace_id,
                "document_id": self.document_id,
                "document_version_id": self.document_version_id,
                "page_number": self.page_number,
                "source_sha256": self.source_sha256,
            }
            if self.corpus == "case":
                required["case_id"] = self.case_id
            missing = [field_name for field_name, value in required.items() if value is None]
            if missing:
                raise ValueError(f"{self.corpus} 引用缺少定位字段: {', '.join(missing)}")
        if self.corpus == "assessment":
            required = {
                "workspace_id": self.workspace_id,
                "case_id": self.case_id,
                "assessment_id": self.assessment_id,
            }
            missing = [field_name for field_name, value in required.items() if value is None]
            if missing:
                raise ValueError(f"Assessment 引用缺少定位字段: {', '.join(missing)}")
        return self


class EvidenceQAClaim(BaseDomainModel):
    """LLM 只能输出原子 Claim 和引用 ID，不直接输出自由长答案。"""

    claim_id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=2000)
    citation_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_claim(self) -> EvidenceQAClaim:
        if not self.text.strip():
            raise ValueError("Claim text 不能为空白字符串")
        if len(self.citation_ids) != len(set(self.citation_ids)):
            raise ValueError("citation_ids 不能重复")
        return self


class ClaimCitationVerification(BaseDomainModel):
    """Claim 与 Citation 的结构覆盖报告。"""

    claim_count: int = Field(ge=0)
    cited_claim_count: int = Field(ge=0)
    coverage: float = Field(ge=0.0, le=1.0)
    uncited_claim_ids: list[str] = Field(default_factory=list)
    unknown_citation_ids: list[str] = Field(default_factory=list)
    unused_citation_ids: list[str] = Field(default_factory=list)
    valid: bool
    method: Literal["structural_v1"] = "structural_v1"

    @model_validator(mode="after")
    def validate_counts(self) -> ClaimCitationVerification:
        if self.cited_claim_count > self.claim_count:
            raise ValueError("cited_claim_count 不能大于 claim_count")
        expected = 1.0 if self.claim_count == 0 else self.cited_claim_count / self.claim_count
        if abs(self.coverage - expected) > 1e-9:
            raise ValueError("coverage 与 Claim 计数不一致")
        expected_valid = (
            self.coverage == 1.0 and not self.uncited_claim_ids and not self.unknown_citation_ids
        )
        if self.valid != expected_valid:
            raise ValueError("valid 与 Claim-Citation 覆盖结果不一致")
        return self


class ClaimSupportJudgement(BaseDomainModel):
    """独立验证器对单个 Claim 的证据支持判定。"""

    claim_id: str = Field(min_length=1, max_length=100)
    supported: bool
    citation_ids: list[str] = Field(default_factory=list)
    reason: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def validate_judgement(self) -> ClaimSupportJudgement:
        if len(self.citation_ids) != len(set(self.citation_ids)):
            raise ValueError("ClaimSupport citation_ids 不能重复")
        if self.supported and not self.citation_ids:
            raise ValueError("supported Claim 必须记录实际支持它的 citation_ids")
        if not self.supported and not self.reason.strip():
            raise ValueError("unsupported Claim 必须说明 reason")
        return self


class ClaimSupportResult(BaseDomainModel):
    """语义支持校验结果；结果层只能移除不受支持的 Claim，不能把它放行。"""

    judgements: list[ClaimSupportJudgement] = Field(default_factory=list)
    unsupported_claim_ids: list[str] = Field(default_factory=list)
    valid: bool
    method: Literal["independent_llm_v1"] = "independent_llm_v1"

    @model_validator(mode="after")
    def validate_result(self) -> ClaimSupportResult:
        claim_ids = [judgement.claim_id for judgement in self.judgements]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("ClaimSupport judgement claim_id 不能重复")
        expected_unsupported = sorted(
            judgement.claim_id for judgement in self.judgements if not judgement.supported
        )
        if self.unsupported_claim_ids != expected_unsupported:
            raise ValueError("unsupported_claim_ids 与 judgements 不一致")
        if self.valid != (not self.unsupported_claim_ids):
            raise ValueError("ClaimSupport valid 与 unsupported_claim_ids 不一致")
        return self


class ClaimRepairReport(BaseDomainModel):
    """结果层有限修复报告；只允许删除坏 Claim，不改写内容或补造引用。"""

    status: ClaimRepairStatus
    original_claim_count: int = Field(ge=0)
    kept_claim_ids: list[str] = Field(default_factory=list)
    removed_claim_ids: list[str] = Field(default_factory=list)
    removal_reasons: dict[str, list[ClaimRemovalReason]] = Field(default_factory=dict)
    method: Literal["bounded_filter_v1"] = "bounded_filter_v1"

    @model_validator(mode="after")
    def validate_report(self) -> ClaimRepairReport:
        if len(self.kept_claim_ids) != len(set(self.kept_claim_ids)):
            raise ValueError("kept_claim_ids 不能重复")
        if len(self.removed_claim_ids) != len(set(self.removed_claim_ids)):
            raise ValueError("removed_claim_ids 不能重复")
        if set(self.kept_claim_ids) & set(self.removed_claim_ids):
            raise ValueError("同一 Claim 不能同时保留和移除")
        if self.original_claim_count != len(self.kept_claim_ids) + len(self.removed_claim_ids):
            raise ValueError("original_claim_count 与保留/移除 Claim 数不一致")
        if set(self.removal_reasons) != set(self.removed_claim_ids):
            raise ValueError("removal_reasons 必须且只能覆盖 removed_claim_ids")
        if any(
            not reasons or len(reasons) != len(set(reasons))
            for reasons in self.removal_reasons.values()
        ):
            raise ValueError("每个移除 Claim 必须有不重复的 removal_reasons")
        if self.status == "not_needed":
            if self.removed_claim_ids:
                raise ValueError("not_needed 不能包含 removed_claim_ids")
        elif self.status == "repaired":
            if not self.kept_claim_ids or not self.removed_claim_ids:
                raise ValueError("repaired 必须同时包含保留和移除 Claim")
        elif self.kept_claim_ids or not self.removed_claim_ids:
            raise ValueError("failed 必须移除全部候选 Claim")
        return self


class EvidenceQADraft(BaseDomainModel):
    """LLM 结构化输出；最终答案由服务端基于已校验 Claim 渲染。"""

    status: EvidenceQAStatus
    claims: list[EvidenceQAClaim] = Field(default_factory=list)
    refusal_reason: str = Field(default="", max_length=2000)
    unanswered_aspects: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_draft(self) -> EvidenceQADraft:
        claim_ids = [claim.claim_id for claim in self.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("claim_id 不能重复")
        if len(self.unanswered_aspects) != len(set(self.unanswered_aspects)):
            raise ValueError("unanswered_aspects 不能重复")
        if self.status == "answered":
            if not self.claims:
                raise ValueError("answered 必须至少包含一个 Claim")
            if self.refusal_reason or self.unanswered_aspects:
                raise ValueError("answered 不能包含拒答原因或未回答部分")
        elif self.status == "partially_answered":
            if not self.claims or not self.unanswered_aspects:
                raise ValueError("partially_answered 必须包含 Claim 和未回答部分")
            if self.refusal_reason:
                raise ValueError("partially_answered 不使用 refusal_reason")
        else:
            if not self.refusal_reason.strip():
                raise ValueError("refused 必须说明 refusal_reason")
            if self.claims or self.unanswered_aspects:
                raise ValueError("refused 不能携带事实 Claim 或未回答部分")
        return self


class EvidenceQAAnswer(BaseDomainModel):
    """经过结构校验后的最终 Evidence QA 结果。"""

    question: str = Field(min_length=1, max_length=2000)
    scope: EvidenceQAScope
    status: EvidenceQAStatus
    claims: list[EvidenceQAClaim] = Field(default_factory=list)
    citations: list[EvidenceQACitation] = Field(default_factory=list)
    refusal_reason: str = Field(default="", max_length=2000)
    unanswered_aspects: list[str] = Field(default_factory=list)
    verification: ClaimCitationVerification
    support_verification: ClaimSupportResult
    repair_report: ClaimRepairReport

    @model_validator(mode="after")
    def validate_answer(self) -> EvidenceQAAnswer:
        if not self.question.strip():
            raise ValueError("question 不能为空白字符串")
        draft = EvidenceQADraft(
            status=self.status,
            claims=self.claims,
            refusal_reason=self.refusal_reason,
            unanswered_aspects=self.unanswered_aspects,
        )
        citation_ids = [citation.citation_id for citation in self.citations]
        if len(citation_ids) != len(set(citation_ids)):
            raise ValueError("citation_id 不能重复")
        expected = ClaimCitationVerifier.verify(draft.claims, self.citations)
        if self.verification != expected:
            raise ValueError("verification 与当前 Claim-Citation 结构不一致")
        if self.status != "refused" and not self.verification.valid:
            raise ValueError("非拒答结果必须通过 Claim-Citation 校验")
        if self.status != "refused":
            expected_claim_ids = {claim.claim_id for claim in self.claims}
            actual_claim_ids = {
                judgement.claim_id for judgement in self.support_verification.judgements
            }
            if actual_claim_ids != expected_claim_ids:
                raise ValueError("support_verification 必须覆盖全部 Claim")
            if not self.support_verification.valid:
                raise ValueError("非拒答结果必须通过 Claim 语义支持校验")
            if self.repair_report.kept_claim_ids != [claim.claim_id for claim in self.claims]:
                raise ValueError("repair_report 必须按顺序记录最终保留的 Claim")
            if self.repair_report.status == "failed":
                raise ValueError("非拒答结果不能使用 failed repair_report")
            if self.repair_report.status == "repaired" and self.status != "partially_answered":
                raise ValueError("修复后的回答必须标记为 partially_answered")
        if self.status == "refused" and self.citations:
            raise ValueError("refused 不返回未被 Claim 使用的引用")
        if self.status == "refused" and self.support_verification.judgements:
            raise ValueError("refused 不返回 Claim 语义判定")
        if self.status == "refused" and self.repair_report.kept_claim_ids:
            raise ValueError("refused 的 repair_report 不能保留 Claim")
        if self.status == "refused" and self.repair_report.status == "repaired":
            raise ValueError("refused 不能使用 repaired repair_report")
        return self

    @property
    def answer(self) -> str:
        """只渲染通过校验的 Claim，避免 LLM 在自由长文本中夹带无引用结论。"""
        if self.status == "refused":
            return self.refusal_reason
        lines: list[str] = []
        if self.status == "partially_answered":
            lines.append("⚠️ 以下回答仅覆盖现有证据能够支持的部分：")
        for index, claim in enumerate(self.claims, start=1):
            markers = "".join(f"[{citation_id}]" for citation_id in claim.citation_ids)
            lines.append(f"{index}. {claim.text}{markers}")
        if self.unanswered_aspects:
            lines.append("尚缺少证据：" + "；".join(self.unanswered_aspects))
        return "\n".join(lines)


class ClaimCitationVerifier:
    """纯函数式结构校验器，不把 LLM 自报的引用视为可信。"""

    @staticmethod
    def verify(
        claims: list[EvidenceQAClaim],
        citations: list[EvidenceQACitation],
    ) -> ClaimCitationVerification:
        known_ids = {citation.citation_id for citation in citations}
        uncited = [claim.claim_id for claim in claims if not claim.citation_ids]
        referenced_ids = {citation_id for claim in claims for citation_id in claim.citation_ids}
        unknown = sorted(referenced_ids - known_ids)
        unused = sorted(known_ids - referenced_ids)
        cited_claim_count = sum(
            bool(claim.citation_ids) and set(claim.citation_ids).issubset(known_ids)
            for claim in claims
        )
        claim_count = len(claims)
        coverage = 1.0 if claim_count == 0 else cited_claim_count / claim_count
        return ClaimCitationVerification(
            claim_count=claim_count,
            cited_claim_count=cited_claim_count,
            coverage=coverage,
            uncited_claim_ids=uncited,
            unknown_citation_ids=unknown,
            unused_citation_ids=unused,
            valid=coverage == 1.0 and not uncited and not unknown,
        )
