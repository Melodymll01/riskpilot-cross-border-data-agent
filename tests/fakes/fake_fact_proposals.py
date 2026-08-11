"""FactProposalGeneratorPort Fake。"""

from __future__ import annotations

from domain.facts import FactProposal, FactProposalDocument


class FakeFactProposalGenerator:
    def __init__(
        self,
        proposals: list[FactProposal] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.proposals = proposals or []
        self.error = error
        self.calls: list[dict[str, object]] = []

    def propose(
        self,
        *,
        field_names: list[str],
        documents: list[FactProposalDocument],
    ) -> list[FactProposal]:
        self.calls.append(
            {
                "field_names": field_names,
                "documents": documents,
            }
        )
        if self.error is not None:
            raise self.error
        return list(self.proposals)
