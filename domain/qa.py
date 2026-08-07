"""V3 Evidence QA 范围、引用、Claim 与结构校验模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from domain.models import BaseDomainModel

QACorpus = Literal["regulatory", "workspace", "case", "assessment"]
EvidenceQAStatus = Literal["answered", "partially_answered", "refused"]


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
        if (
            any(corpus in {"workspace", "case", "assessment"} for corpus in self.corpora)
            and not self.workspace_id
        ):
            raise ValueError("Workspace、Case 或 Assessment 范围必须提供 workspace_id")
        if any(corpus in {"case", "assessment"} for corpus in self.corpora) and not self.case_id:
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
    assessment_id: str | None = None
    clause_id: str | None = None
    score: float = Field(default=0.0, ge=0.0)

    @model_validator(mode="after")
    def validate_source_location(self) -> EvidenceQACitation:
        if not self.quote.strip():
            raise ValueError("quote 不能为空白字符串")
        if self.corpus == "workspace" and self.workspace_id is None:
            raise ValueError("Workspace 引用必须提供 workspace_id")
        if self.corpus == "case":
            required = {
                "workspace_id": self.workspace_id,
                "case_id": self.case_id,
                "document_id": self.document_id,
                "document_version_id": self.document_version_id,
                "page_number": self.page_number,
            }
            missing = [field_name for field_name, value in required.items() if value is None]
            if missing:
                raise ValueError(f"Case 引用缺少定位字段: {', '.join(missing)}")
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
        if self.status == "refused" and self.citations:
            raise ValueError("refused 不返回未被 Claim 使用的引用")
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
