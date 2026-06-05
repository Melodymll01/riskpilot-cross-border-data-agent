"""`EvidencePort` 的 mock 实现。

PR-3 阶段 evidence 服务尚未对接，先以确定性 mock 让上层 Agent 能跑通 6 步链路。
真实 HTTP 客户端将在后续 step 添加 `HttpEvidenceClient`，签名保持一致。
"""

from __future__ import annotations

from domain.models import EvidenceJudgement


class MockEvidenceClient:
    """根据 factor_id 哈希返回稳定的 EvidenceJudgement。"""

    _LABELS = ("low", "moderate", "high")

    def __init__(self, default_confidence: float = 0.6) -> None:
        if not 0.0 <= default_confidence <= 1.0:
            msg = f"default_confidence 必须 ∈ [0,1]，得到 {default_confidence}"
            raise ValueError(msg)
        self._default_confidence = default_confidence

    def judge(self, factor_id: str, context: dict[str, str]) -> EvidenceJudgement:
        # 简单稳定的 label 选择：按 factor_id 字符 ord 之和模 3
        idx = sum(ord(c) for c in factor_id) % len(self._LABELS)
        label = self._LABELS[idx]
        rationale = (
            f"[mock] factor={factor_id} | "
            f"context_keys={sorted(context.keys())}"
        )
        return EvidenceJudgement(
            factor_id=factor_id,
            label=label,
            rationale=rationale,
            confidence=self._default_confidence,
        )
