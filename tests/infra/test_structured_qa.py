"""结构化 Evidence QA 生成与独立支持校验适配器测试。"""

from __future__ import annotations

import pytest

from domain import (
    ClaimSupportVerifierPort,
    EvidenceQACitation,
    EvidenceQAClaim,
    EvidenceQAGeneratorPort,
)
from infra.qa import StructuredClaimSupportVerifier, StructuredEvidenceQAGenerator
from tests.fakes import FakeChat


def _citation(citation_id: str = "E1") -> EvidenceQACitation:
    return EvidenceQACitation(
        citation_id=citation_id,
        corpus="regulatory",
        source_id=f"source_{citation_id}",
        source_name="个人信息保护法",
        title="第三十八条",
        quote="个人信息处理者向境外提供个人信息，应当具备法定条件之一。",
    )


def _claim() -> EvidenceQAClaim:
    return EvidenceQAClaim(
        claim_id="C1",
        text="个人信息出境应具备法定条件之一。",
        citation_ids=["E1"],
    )


class TestStructuredEvidenceQAGenerator:
    def test_satisfies_port_and_parses_claims(self) -> None:
        chat = FakeChat(
            responses=[
                """
                {
                  "status": "answered",
                  "claims": [
                    {
                      "claim_id": "C1",
                      "text": "个人信息出境应具备法定条件之一。",
                      "citation_ids": ["E1"]
                    }
                  ],
                  "refusal_reason": "",
                  "unanswered_aspects": []
                }
                """
            ]
        )
        generator = StructuredEvidenceQAGenerator(chat)
        assert isinstance(generator, EvidenceQAGeneratorPort)

        draft = generator.generate(
            question="个人信息能否出境？",
            citations=[_citation()],
        )

        assert draft.status == "answered"
        assert draft.claims == [_claim()]
        assert chat.calls[0]["temperature"] == 0.0
        assert chat.calls[0]["json_mode"] is True
        user_prompt = chat.calls[0]["messages"][1]["content"]
        assert "[E1]" in user_prompt
        assert "第三十八条" in user_prompt

    def test_no_citation_refuses_without_llm_call(self) -> None:
        chat = FakeChat()
        draft = StructuredEvidenceQAGenerator(chat).generate(
            question="问题",
            citations=[],
        )
        assert draft.status == "refused"
        assert chat.calls == []

    def test_code_fenced_json_is_supported(self) -> None:
        chat = FakeChat(
            responses=[
                """```json
                {
                  "status":"refused",
                  "claims":[],
                  "refusal_reason":"证据不足",
                  "unanswered_aspects":[]
                }
                ```"""
            ]
        )
        draft = StructuredEvidenceQAGenerator(chat).generate(
            question="问题",
            citations=[_citation()],
        )
        assert draft.status == "refused"

    def test_invalid_json_or_schema_rejected(self) -> None:
        for response in ("not json", '{"status":"answered","claims":[]}'):
            generator = StructuredEvidenceQAGenerator(FakeChat(responses=[response]))
            with pytest.raises(ValueError):
                generator.generate(question="问题", citations=[_citation()])


class TestStructuredClaimSupportVerifier:
    def test_satisfies_port_and_accepts_supported_claim(self) -> None:
        chat = FakeChat(
            responses=[
                """
                {
                  "judgements": [
                    {
                      "claim_id": "C1",
                      "supported": true,
                      "citation_ids": ["E1"],
                      "reason": ""
                    }
                  ]
                }
                """
            ]
        )
        verifier = StructuredClaimSupportVerifier(chat)
        assert isinstance(verifier, ClaimSupportVerifierPort)

        result = verifier.verify([_claim()], [_citation()])

        assert result.valid is True
        assert result.unsupported_claim_ids == []
        assert chat.calls[0]["json_mode"] is True
        assert "声明引用: E1" in chat.calls[0]["messages"][1]["content"]

    def test_unsupported_claim_fails_closed(self) -> None:
        verifier = StructuredClaimSupportVerifier(
            FakeChat(
                responses=[
                    """
                    {
                      "judgements": [
                        {
                          "claim_id": "C1",
                          "supported": false,
                          "citation_ids": [],
                          "reason": "原文未明确支持"
                        }
                      ]
                    }
                    """
                ]
            )
        )
        result = verifier.verify([_claim()], [_citation()])
        assert result.valid is False
        assert result.unsupported_claim_ids == ["C1"]

    def test_missing_claim_judgement_rejected(self) -> None:
        verifier = StructuredClaimSupportVerifier(FakeChat(responses=['{"judgements":[]}']))
        with pytest.raises(ValueError, match="覆盖全部"):
            verifier.verify([_claim()], [_citation()])

    def test_unknown_or_expanded_citation_rejected(self) -> None:
        for citation_id in ("UNKNOWN", "E2"):
            verifier = StructuredClaimSupportVerifier(
                FakeChat(
                    responses=[
                        f"""
                        {{
                          "judgements": [
                            {{
                              "claim_id": "C1",
                              "supported": true,
                              "citation_ids": ["{citation_id}"],
                              "reason": ""
                            }}
                          ]
                        }}
                        """
                    ]
                )
            )
            citations = [_citation()]
            if citation_id == "E2":
                citations.append(_citation("E2"))
            with pytest.raises(ValueError):
                verifier.verify([_claim()], citations)

    def test_no_claims_short_circuits_without_llm(self) -> None:
        chat = FakeChat()
        result = StructuredClaimSupportVerifier(chat).verify([], [])
        assert result.valid is True
        assert result.judgements == []
        assert chat.calls == []
