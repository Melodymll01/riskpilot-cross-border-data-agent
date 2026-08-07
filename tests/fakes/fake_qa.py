"""Evidence QA 生成器与 Claim 支持验证器 Fake。"""

from __future__ import annotations

from domain.qa import (
    ClaimSupportJudgement,
    ClaimSupportResult,
    EvidenceQACitation,
    EvidenceQAClaim,
    EvidenceQADraft,
)


class FakeEvidenceQAGenerator:
    def __init__(
        self,
        draft: EvidenceQADraft | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.draft = draft or EvidenceQADraft(
            status="answered",
            claims=[
                EvidenceQAClaim(
                    claim_id="C1",
                    text="证据支持该结论。",
                    citation_ids=["E1"],
                )
            ],
        )
        self.error = error
        self.calls: list[dict[str, object]] = []

    def generate(
        self,
        *,
        question: str,
        citations: list[EvidenceQACitation],
    ) -> EvidenceQADraft:
        self.calls.append({"question": question, "citations": citations})
        if self.error is not None:
            raise self.error
        return self.draft


class FakeClaimSupportVerifier:
    def __init__(
        self,
        result: ClaimSupportResult | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.result = result or ClaimSupportResult(
            judgements=[
                ClaimSupportJudgement(
                    claim_id="C1",
                    supported=True,
                    citation_ids=["E1"],
                )
            ],
            unsupported_claim_ids=[],
            valid=True,
        )
        self.error = error
        self.calls: list[dict[str, object]] = []

    def verify(
        self,
        claims: list[EvidenceQAClaim],
        citations: list[EvidenceQACitation],
    ) -> ClaimSupportResult:
        self.calls.append({"claims": claims, "citations": citations})
        if self.error is not None:
            raise self.error
        return self.result
