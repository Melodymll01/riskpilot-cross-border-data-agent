"""FactProposalGeneratorPort Fake。"""

from __future__ import annotations

from domain.facts import FactProposal, FactProposalDocument, FactProposalResult


class FakeFactProposalGenerator:
    def __init__(
        self,
        proposals: list[FactProposal] | None = None,
        *,
        error: Exception | None = None,
        token_usage: int = 0,
    ) -> None:
        self.proposals = proposals or []
        self.error = error
        self.token_usage = token_usage
        self.calls: list[dict[str, object]] = []

    def propose(
        self,
        *,
        field_names: list[str],
        documents: list[FactProposalDocument],
        max_tokens: int | None = None,
    ) -> FactProposalResult:
        self.calls.append(
            {
                "field_names": field_names,
                "documents": documents,
                "max_tokens": max_tokens,
            }
        )
        if self.error is not None:
            raise self.error
        return FactProposalResult(
            proposals=list(self.proposals),
            token_usage=self.token_usage,
        )
