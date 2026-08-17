"""safe_empty Fact Proposal profile 只触发 HITL，不调用模型。"""

from __future__ import annotations

from domain import FactProposalDocument, FactProposalGeneratorPort, FactProposalPage
from infra.qa import SafeEmptyFactProposalGenerator


def test_safe_empty_generator_returns_no_predictions_or_usage() -> None:
    generator = SafeEmptyFactProposalGenerator()

    result = generator.propose(
        field_names=["important_data_involved"],
        documents=[
            FactProposalDocument(
                document_id="doc_001",
                document_version_id="ver_001",
                source_name="synthetic.txt",
                source_sha256="a" * 64,
                pages=[
                    FactProposalPage(
                        page_number=1,
                        content="材料没有说明是否涉及重要数据。",
                    )
                ],
            )
        ],
        max_tokens=100,
    )

    assert isinstance(generator, FactProposalGeneratorPort)
    assert result.proposals == []
    assert result.input_tokens == 0
    assert result.output_tokens == 0
    assert result.token_usage == 0
