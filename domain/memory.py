"""长期记忆召回策略。

纯领域策略只接收已获得的候选事实和语义分数，不依赖向量库、模型或系统时间，
便于生产调用、离线评测和面试演示复用同一套排序逻辑。
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from domain.models import Fact, MemoryRecallHit, MemoryRecallTrace

_SECONDS_PER_DAY = 86400.0


@dataclass(frozen=True)
class MemoryRecallPolicy:
    """把语义相关性、置信度、显著性和新鲜度融合为可解释召回分数。"""

    strategy_version: str = "hybrid_v1"
    semantic_weight: float = 0.65
    confidence_weight: float = 0.15
    salience_weight: float = 0.15
    freshness_weight: float = 0.05
    min_semantic_score: float = 0.25
    min_final_score: float = 0.35
    freshness_half_life_days: float = 90.0

    def rank(
        self,
        *,
        owner_id: str,
        query: str,
        candidates: list[tuple[Fact, float]],
        k: int,
        now: float,
        ttl_days: float,
    ) -> MemoryRecallTrace:
        """过滤并排序候选，返回不含向量和 Prompt 的可解释轨迹。"""
        rejected: Counter[str] = Counter()
        scored: list[MemoryRecallHit] = []
        cutoff = now - ttl_days * _SECONDS_PER_DAY if ttl_days > 0 else None
        weights = self._normalized_weights()

        for fact, raw_semantic_score in candidates:
            if fact.owner_id != owner_id:
                rejected["owner_mismatch"] += 1
                continue
            if fact.superseded_by is not None:
                rejected["superseded"] += 1
                continue
            if cutoff is not None and fact.created_at < cutoff:
                rejected["expired"] += 1
                continue

            semantic_score = self._clamp01(raw_semantic_score)
            if semantic_score < self.min_semantic_score:
                rejected["low_semantic_score"] += 1
                continue

            freshness_score = self._freshness_score(fact, now)
            final_score = (
                semantic_score * weights["semantic"]
                + fact.confidence * weights["confidence"]
                + fact.salience * weights["salience"]
                + freshness_score * weights["freshness"]
            )
            if final_score < self.min_final_score:
                rejected["low_final_score"] += 1
                continue
            scored.append(
                MemoryRecallHit(
                    rank=1,
                    fact=fact,
                    semantic_score=semantic_score,
                    confidence_score=fact.confidence,
                    salience_score=fact.salience,
                    freshness_score=freshness_score,
                    final_score=final_score,
                )
            )

        scored.sort(
            key=lambda hit: (
                hit.final_score,
                hit.semantic_score,
                hit.confidence_score,
                hit.salience_score,
                hit.fact.created_at,
                hit.fact.fact_id,
            ),
            reverse=True,
        )
        eligible_count = len(scored)
        selected = [
            hit.model_copy(update={"rank": rank})
            for rank, hit in enumerate(scored[: max(0, k)], start=1)
        ]
        return MemoryRecallTrace(
            owner_id=owner_id,
            query=query,
            strategy_version=self.strategy_version,
            candidate_count=len(candidates),
            eligible_count=eligible_count,
            rejected_counts=dict(sorted(rejected.items())),
            hits=selected,
        )

    def _freshness_score(self, fact: Fact, now: float) -> float:
        if self.freshness_half_life_days <= 0:
            return 1.0
        reference_time = max(fact.created_at, fact.last_used_at)
        age_days = max(0.0, (now - reference_time) / _SECONDS_PER_DAY)
        return math.exp(-math.log(2.0) * age_days / self.freshness_half_life_days)

    def _normalized_weights(self) -> dict[str, float]:
        raw = {
            "semantic": max(0.0, self.semantic_weight),
            "confidence": max(0.0, self.confidence_weight),
            "salience": max(0.0, self.salience_weight),
            "freshness": max(0.0, self.freshness_weight),
        }
        total = sum(raw.values())
        if total <= 0:
            return {"semantic": 1.0, "confidence": 0.0, "salience": 0.0, "freshness": 0.0}
        return {name: value / total for name, value in raw.items()}

    @staticmethod
    def _clamp01(value: float) -> float:
        if not math.isfinite(value):
            return 0.0
        return max(0.0, min(1.0, float(value)))
