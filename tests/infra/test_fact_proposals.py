"""结构化 Fact 提议生成器测试。"""

from __future__ import annotations

import pytest

from domain import (
    FactProposalDocument,
    FactProposalGeneratorPort,
    FactProposalPage,
)
from infra.qa import StructuredFactProposalGenerator
from tests.fakes import FakeChat


def _document() -> FactProposalDocument:
    return FactProposalDocument(
        document_id="doc_001",
        document_version_id="ver_001",
        source_name="source.txt",
        source_sha256="a" * 64,
        pages=[
            FactProposalPage(
                page_number=1,
                content="材料明确说明涉及重要数据。",
            )
        ],
    )


class TestStructuredFactProposalGenerator:
    def test_satisfies_port_and_parses_proposal(self) -> None:
        chat = FakeChat(
            responses=[
                """
                {
                  "proposals": [
                    {
                      "field_name": "important_data_involved",
                      "value": true,
                      "confidence": 0.95,
                      "evidence": [
                        {
                          "document_id": "doc_001",
                          "document_version_id": "ver_001",
                          "page_number": 1,
                          "quote": "涉及重要数据",
                          "confidence": 0.95
                        }
                      ]
                    }
                  ]
                }
                """
            ]
        )
        generator = StructuredFactProposalGenerator(chat)
        assert isinstance(generator, FactProposalGeneratorPort)

        result = generator.propose(
            field_names=["important_data_involved"],
            documents=[_document()],
        )

        assert result.proposals[0].field_name == "important_data_involved"
        assert result.proposals[0].evidence[0].quote == "涉及重要数据"
        assert result.token_usage == 0
        assert chat.calls[0]["temperature"] == 0.0
        assert chat.calls[0]["json_mode"] is True
        system_prompt = chat.calls[0]["messages"][0]["content"]
        assert "不可信数据" in system_prompt
        user_prompt = chat.calls[0]["messages"][1]["content"]
        assert "important_data_involved" in user_prompt
        assert "材料明确说明涉及重要数据" in user_prompt

    def test_field_outside_allowlist_rejected(self) -> None:
        generator = StructuredFactProposalGenerator(
            FakeChat(
                responses=[
                    """
                    {
                      "proposals": [
                        {
                          "field_name": "invented_field",
                          "value": true,
                          "confidence": 0.8,
                          "evidence": [{
                            "document_id": "doc_001",
                            "document_version_id": "ver_001",
                            "page_number": 1,
                            "quote": "涉及重要数据"
                          }]
                        }
                      ]
                    }
                    """
                ]
            )
        )
        with pytest.raises(ValueError, match="白名单"):
            generator.propose(
                field_names=["important_data_involved"],
                documents=[_document()],
            )

    def test_duplicate_field_or_invalid_json_rejected(self) -> None:
        proposal = """
          {
            "field_name": "important_data_involved",
            "value": true,
            "confidence": 0.8,
            "evidence": [{
              "document_id": "doc_001",
              "document_version_id": "ver_001",
              "page_number": 1,
              "quote": "涉及重要数据"
            }]
          }
        """
        duplicate = StructuredFactProposalGenerator(
            FakeChat(responses=[f'{{"proposals":[{proposal},{proposal}]}}'])
        )
        with pytest.raises(ValueError, match="只能返回一个"):
            duplicate.propose(
                field_names=["important_data_involved"],
                documents=[_document()],
            )

        invalid = StructuredFactProposalGenerator(FakeChat(responses=["not-json"]))
        with pytest.raises(ValueError, match="非法 JSON"):
            invalid.propose(
                field_names=["important_data_involved"],
                documents=[_document()],
            )

    def test_empty_documents_short_circuits_without_llm(self) -> None:
        chat = FakeChat()
        result = StructuredFactProposalGenerator(chat).propose(
            field_names=["important_data_involved"],
            documents=[],
        )
        assert result.proposals == []
        assert result.token_usage == 0
        assert chat.calls == []

    def test_returns_real_usage_and_forwards_token_limit(self) -> None:
        chat = FakeChat(
            responses=['{"proposals": []}'],
            token_usages=[80],
        )

        result = StructuredFactProposalGenerator(chat).propose(
            field_names=["important_data_involved"],
            documents=[_document()],
            max_tokens=120,
        )

        assert result.token_usage == 80
        assert chat.calls[0]["max_tokens"] == 120
