"""Evidence QA 生成器与 Claim 支持验证器 Fake。"""

from __future__ import annotations

from domain.ports import RunControlPort
from domain.qa import (
    ClaimSupportJudgement,
    ClaimSupportResult,
    EvidenceQACitation,
    EvidenceQAClaim,
    EvidenceQADraft,
    UserContextMessage,
)


class FakeEvidenceQAGenerator:
    def __init__(
        self,
        draft: EvidenceQADraft | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.draft = draft
        self.error = error
        self.calls: list[dict[str, object]] = []

    def generate(
        self,
        *,
        question: str,
        citations: list[EvidenceQACitation],
        user_context: list[UserContextMessage] | None = None,
        preferences: list[str] | None = None,
        runtime_control: RunControlPort | None = None,
    ) -> EvidenceQADraft:
        def invoke() -> EvidenceQADraft:
            self.calls.append(
                {
                    "question": question,
                    "citations": citations,
                    "user_context": [item.model_dump() for item in user_context or []],
                    "preferences": preferences or [],
                    "runtime_control": runtime_control,
                }
            )
            if self.error is not None:
                raise self.error
            if self.draft is not None:
                return self.draft
            citation_ids = [citations[0].citation_id] if citations else []
            return EvidenceQADraft(
                status="answered",
                claims=[
                    EvidenceQAClaim(
                        claim_id="C1",
                        text="证据支持该结论。",
                        citation_ids=citation_ids,
                    )
                ],
            )

        return (
            runtime_control.call_model(
                operation="generation",
                stage="generation",
                invoke=invoke,
            )
            if runtime_control is not None
            else invoke()
        )


class FakeClaimSupportVerifier:
    def __init__(
        self,
        result: ClaimSupportResult | None = None,
        *,
        error: Exception | None = None,
        started_event=None,
        release_event=None,
    ) -> None:
        self.result = result
        self.error = error
        self.started_event = started_event
        self.release_event = release_event
        self.calls: list[dict[str, object]] = []

    def verify(
        self,
        claims: list[EvidenceQAClaim],
        citations: list[EvidenceQACitation],
        user_context: list[UserContextMessage] | None = None,
        runtime_control: RunControlPort | None = None,
    ) -> ClaimSupportResult:
        def invoke() -> ClaimSupportResult:
            if self.started_event is not None:
                self.started_event.set()
            if self.release_event is not None:
                self.release_event.wait(timeout=5.0)
            self.calls.append(
                {
                    "claims": claims,
                    "citations": citations,
                    "user_context": [item.model_dump() for item in user_context or []],
                    "runtime_control": runtime_control,
                }
            )
            if self.error is not None:
                raise self.error
            if self.result is not None:
                return self.result
            return ClaimSupportResult(
                judgements=[
                    ClaimSupportJudgement(
                        claim_id=claim.claim_id,
                        supported=True,
                        citation_ids=list(claim.citation_ids),
                    )
                    for claim in claims
                ],
                unsupported_claim_ids=[],
                valid=True,
            )

        return (
            runtime_control.call_model(
                operation="verification",
                stage="verification",
                invoke=invoke,
            )
            if runtime_control is not None
            else invoke()
        )
