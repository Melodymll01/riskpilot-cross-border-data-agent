"""RiskProfilePort Fake。"""

from __future__ import annotations

from domain.models import EvidenceSpan, RiskProfile


class FakeRiskProfile:
    def __init__(self, result: RiskProfile | None = None) -> None:
        self._result = result
        self.calls: list[dict[str, object]] = []

    def assess(
        self,
        target: str,
        document: str | None = None,
        *,
        language: str = "zh",
    ) -> RiskProfile:
        self.calls.append(
            {
                "target": target,
                "document": document,
                "language": language,
            }
        )
        return self._result or RiskProfile(
            target=target,
            evidence_state="supported",
            evidence_spans=[EvidenceSpan(text=document or target)],
            explanation="fake risk profile",
            metadata={"fake": True},
        )
