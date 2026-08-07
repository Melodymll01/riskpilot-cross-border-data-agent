"""V3 Evidence QA 范围、引用与 Claim-Citation 校验测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from domain.qa import (
    ClaimCitationVerifier,
    ClaimSupportJudgement,
    ClaimSupportResult,
    EvidenceQAAnswer,
    EvidenceQACitation,
    EvidenceQAClaim,
    EvidenceQADraft,
    EvidenceQAScope,
)


def _case_citation(**overrides: object) -> EvidenceQACitation:
    values: dict[str, object] = {
        "citation_id": "E1",
        "corpus": "case",
        "source_id": "chunk_001",
        "source_name": "case.txt",
        "quote": "境外接收方应承担安全保护责任",
        "workspace_id": "ws_001",
        "case_id": "case_001",
        "document_id": "doc_001",
        "document_version_id": "ver_001",
        "page_number": 2,
        "source_sha256": "a" * 64,
        "score": 0.9,
    }
    values.update(overrides)
    return EvidenceQACitation(**values)  # type: ignore[arg-type]


def _claim(**overrides: object) -> EvidenceQAClaim:
    values: dict[str, object] = {
        "claim_id": "C1",
        "text": "境外接收方应承担安全保护责任。",
        "citation_ids": ["E1"],
    }
    values.update(overrides)
    return EvidenceQAClaim(**values)  # type: ignore[arg-type]


class TestEvidenceQAScope:
    def test_regulatory_scope_needs_no_workspace(self) -> None:
        scope = EvidenceQAScope(corpora=["regulatory"])
        assert scope.workspace_id is None
        with pytest.raises(ValidationError, match="不接受 workspace_id"):
            EvidenceQAScope(corpora=["regulatory"], workspace_id="ws_001")

    def test_case_scope_requires_workspace_and_case(self) -> None:
        with pytest.raises(ValidationError, match="workspace_id"):
            EvidenceQAScope(corpora=["case"], case_id="case_001")
        with pytest.raises(ValidationError, match="case_id"):
            EvidenceQAScope(corpora=["case"], workspace_id="ws_001")

    def test_assessment_scope_requires_full_parent_chain(self) -> None:
        with pytest.raises(ValidationError, match="assessment_id"):
            EvidenceQAScope(
                corpora=["assessment"],
                workspace_id="ws_001",
                case_id="case_001",
            )

    def test_duplicate_corpora_rejected(self) -> None:
        with pytest.raises(ValidationError, match="corpora"):
            EvidenceQAScope(corpora=["regulatory", "regulatory"])
        with pytest.raises(ValidationError, match="assessment_id"):
            EvidenceQAScope(
                corpora=["case"],
                workspace_id="ws_001",
                case_id="case_001",
                assessment_id="assessment_001",
            )


class TestEvidenceQACitation:
    def test_case_citation_requires_versioned_page_location(self) -> None:
        with pytest.raises(ValidationError, match="page_number"):
            _case_citation(page_number=None)
        with pytest.raises(ValidationError, match="document_version_id"):
            _case_citation(document_version_id=None)
        with pytest.raises(ValidationError, match="source_sha256"):
            _case_citation(source_sha256=None)

    def test_assessment_citation_requires_assessment_id(self) -> None:
        with pytest.raises(ValidationError, match="assessment_id"):
            EvidenceQACitation(
                citation_id="A1",
                corpus="assessment",
                source_id="finding_001",
                source_name="Assessment v1",
                quote="规则已触发",
                workspace_id="ws_001",
                case_id="case_001",
            )

    def test_blank_quote_rejected(self) -> None:
        with pytest.raises(ValidationError, match="quote"):
            _case_citation(quote=" ")


class TestEvidenceQADraft:
    def test_answered_requires_claim(self) -> None:
        with pytest.raises(ValidationError, match="Claim"):
            EvidenceQADraft(status="answered")

    def test_partial_requires_unanswered_aspects(self) -> None:
        with pytest.raises(ValidationError, match="未回答"):
            EvidenceQADraft(status="partially_answered", claims=[_claim()])

    def test_refused_cannot_smuggle_claims(self) -> None:
        with pytest.raises(ValidationError, match="不能携带"):
            EvidenceQADraft(
                status="refused",
                claims=[_claim()],
                refusal_reason="证据不足",
            )

    def test_duplicate_claim_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="claim_id"):
            EvidenceQADraft(status="answered", claims=[_claim(), _claim()])


def _support_result(
    *,
    supported: bool = True,
    claim_id: str = "C1",
    citation_ids: list[str] | None = None,
) -> ClaimSupportResult:
    judgement = ClaimSupportJudgement(
        claim_id=claim_id,
        supported=supported,
        citation_ids=(["E1"] if supported else []) if citation_ids is None else citation_ids,
        reason="" if supported else "原文不支持",
    )
    unsupported = [] if supported else [claim_id]
    return ClaimSupportResult(
        judgements=[judgement],
        unsupported_claim_ids=unsupported,
        valid=supported,
    )


class TestClaimCitationVerifier:
    def test_full_coverage_is_valid(self) -> None:
        verification = ClaimCitationVerifier.verify([_claim()], [_case_citation()])
        assert verification.valid is True
        assert verification.coverage == 1.0
        assert verification.uncited_claim_ids == []
        assert verification.unknown_citation_ids == []

    def test_uncited_claim_blocks_validity(self) -> None:
        verification = ClaimCitationVerifier.verify(
            [_claim(citation_ids=[])],
            [_case_citation()],
        )
        assert verification.valid is False
        assert verification.coverage == 0.0
        assert verification.uncited_claim_ids == ["C1"]
        assert verification.unused_citation_ids == ["E1"]

    def test_unknown_citation_id_blocks_validity(self) -> None:
        verification = ClaimCitationVerifier.verify(
            [_claim(citation_ids=["UNKNOWN"])],
            [_case_citation()],
        )
        assert verification.valid is False
        assert verification.unknown_citation_ids == ["UNKNOWN"]
        assert verification.coverage == 0.0

    def test_unused_citation_does_not_invalidate_supported_claim(self) -> None:
        verification = ClaimCitationVerifier.verify(
            [_claim()],
            [_case_citation(), _case_citation(citation_id="E2", source_id="chunk_002")],
        )
        assert verification.valid is True
        assert verification.unused_citation_ids == ["E2"]

    def test_empty_refusal_has_vacuous_structural_coverage(self) -> None:
        verification = ClaimCitationVerifier.verify([], [])
        assert verification.valid is True
        assert verification.coverage == 1.0


class TestEvidenceQAAnswer:
    def test_answer_renders_only_verified_claims(self) -> None:
        claim = _claim()
        citation = _case_citation()
        answer = EvidenceQAAnswer(
            question="境外接收方有什么义务？",
            scope=EvidenceQAScope(
                corpora=["case"],
                workspace_id="ws_001",
                case_id="case_001",
            ),
            status="answered",
            claims=[claim],
            citations=[citation],
            verification=ClaimCitationVerifier.verify([claim], [citation]),
            support_verification=_support_result(),
        )
        assert answer.answer == "1. 境外接收方应承担安全保护责任。[E1]"
        assert "document_version_id" in answer.model_dump()["citations"][0]

    def test_partial_answer_marks_missing_evidence(self) -> None:
        claim = _claim()
        citation = _case_citation()
        answer = EvidenceQAAnswer(
            question="材料是否完整？",
            scope=EvidenceQAScope(
                corpora=["case"],
                workspace_id="ws_001",
                case_id="case_001",
            ),
            status="partially_answered",
            claims=[claim],
            citations=[citation],
            unanswered_aspects=["未找到保存期限"],
            verification=ClaimCitationVerifier.verify([claim], [citation]),
            support_verification=_support_result(),
        )
        assert answer.answer.startswith("⚠️")
        assert "未找到保存期限" in answer.answer

    def test_non_refusal_rejects_invalid_verification(self) -> None:
        claim = _claim(citation_ids=[])
        citation = _case_citation()
        with pytest.raises(ValidationError, match="必须通过"):
            EvidenceQAAnswer(
                question="问题",
                scope=EvidenceQAScope(corpora=["regulatory"]),
                status="answered",
                claims=[claim],
                citations=[citation],
                verification=ClaimCitationVerifier.verify([claim], [citation]),
                support_verification=_support_result(),
            )

    def test_refusal_has_no_citations_or_claims(self) -> None:
        verification = ClaimCitationVerifier.verify([], [])
        answer = EvidenceQAAnswer(
            question="未知问题",
            scope=EvidenceQAScope(corpora=["regulatory"]),
            status="refused",
            refusal_reason="根据当前检索范围未找到足够证据。",
            verification=verification,
            support_verification=ClaimSupportResult(
                judgements=[],
                unsupported_claim_ids=[],
                valid=True,
            ),
        )
        assert answer.answer == "根据当前检索范围未找到足够证据。"
        assert answer.citations == []

    def test_verification_cannot_be_forged(self) -> None:
        claim = _claim()
        citation = _case_citation()
        forged = ClaimCitationVerifier.verify([], [])
        with pytest.raises(ValidationError, match="verification"):
            EvidenceQAAnswer(
                question="问题",
                scope=EvidenceQAScope(corpora=["case"], workspace_id="ws_001", case_id="case_001"),
                status="answered",
                claims=[claim],
                citations=[citation],
                verification=forged,
                support_verification=_support_result(),
            )

    def test_support_verification_must_cover_every_claim(self) -> None:
        claim = _claim()
        citation = _case_citation()
        with pytest.raises(ValidationError, match="覆盖全部"):
            EvidenceQAAnswer(
                question="问题",
                scope=EvidenceQAScope(
                    corpora=["case"],
                    workspace_id="ws_001",
                    case_id="case_001",
                ),
                status="answered",
                claims=[claim],
                citations=[citation],
                verification=ClaimCitationVerifier.verify([claim], [citation]),
                support_verification=ClaimSupportResult(
                    judgements=[],
                    unsupported_claim_ids=[],
                    valid=True,
                ),
            )

    def test_unsupported_claim_blocks_answer(self) -> None:
        claim = _claim()
        citation = _case_citation()
        with pytest.raises(ValidationError, match="语义支持"):
            EvidenceQAAnswer(
                question="问题",
                scope=EvidenceQAScope(
                    corpora=["case"],
                    workspace_id="ws_001",
                    case_id="case_001",
                ),
                status="answered",
                claims=[claim],
                citations=[citation],
                verification=ClaimCitationVerifier.verify([claim], [citation]),
                support_verification=_support_result(supported=False),
            )
