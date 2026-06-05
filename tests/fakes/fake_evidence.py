"""`EvidencePort` Fake：按 factor_id 返回预设 EvidenceJudgement。"""

from __future__ import annotations

from domain.models import EvidenceJudgement


class FakeEvidence:
    """默认所有 factor 返回 ``label="moderate", confidence=0.5``。"""

    def __init__(
        self,
        responses: dict[str, EvidenceJudgement] | None = None,
    ) -> None:
        self._responses = dict(responses) if responses else {}
        self.calls: list[tuple[str, dict[str, str]]] = []

    def judge(self, factor_id: str, context: dict[str, str]) -> EvidenceJudgement:
        self.calls.append((factor_id, dict(context)))
        if factor_id in self._responses:
            return self._responses[factor_id]
        return EvidenceJudgement(
            factor_id=factor_id,
            label="moderate",
            rationale="fake",
            confidence=0.5,
        )
