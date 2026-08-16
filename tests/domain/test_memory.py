"""长期记忆 hybrid_v1 召回策略测试。"""

from __future__ import annotations

import time

from domain.memory import MemoryRecallPolicy
from domain.models import Fact

_NOW = time.time()


def _fact(
    fact_id: str,
    *,
    owner_id: str = "user:alice",
    confidence: float = 0.5,
    salience: float = 0.5,
    age_days: float = 0.0,
    superseded_by: str | None = None,
) -> Fact:
    timestamp = _NOW - age_days * 86400.0
    return Fact(
        fact_id=fact_id,
        owner_id=owner_id,
        text=f"memory {fact_id}",
        confidence=confidence,
        salience=salience,
        created_at=timestamp,
        last_used_at=timestamp,
        superseded_by=superseded_by,
    )


def test_hybrid_score_can_promote_more_trustworthy_fact() -> None:
    policy = MemoryRecallPolicy()

    trace = policy.rank(
        owner_id="user:alice",
        query="语言偏好",
        candidates=[
            (
                _fact(
                    "tentative",
                    confidence=0.1,
                    salience=0.2,
                    age_days=200,
                ),
                0.86,
            ),
            (
                _fact(
                    "confirmed",
                    confidence=1.0,
                    salience=1.0,
                    age_days=2,
                ),
                0.80,
            ),
        ],
        k=1,
        now=_NOW,
        ttl_days=365.0,
    )

    assert [hit.fact.fact_id for hit in trace.hits] == ["confirmed"]
    assert trace.hits[0].final_score > 0.8


def test_safety_filters_are_explained_without_returning_forbidden_facts() -> None:
    policy = MemoryRecallPolicy()

    trace = policy.rank(
        owner_id="user:alice",
        query="行业",
        candidates=[
            (_fact("other", owner_id="user:bob"), 0.99),
            (_fact("superseded", superseded_by="new"), 0.98),
            (_fact("expired", age_days=500), 0.97),
            (_fact("irrelevant"), 0.10),
            (_fact("allowed", confidence=0.9, salience=0.9), 0.85),
        ],
        k=3,
        now=_NOW,
        ttl_days=365.0,
    )

    assert [hit.fact.fact_id for hit in trace.hits] == ["allowed"]
    assert trace.candidate_count == 5
    assert trace.eligible_count == 1
    assert trace.rejected_counts == {
        "expired": 1,
        "low_semantic_score": 1,
        "owner_mismatch": 1,
        "superseded": 1,
    }


def test_zero_weights_fall_back_to_semantic_ranking() -> None:
    policy = MemoryRecallPolicy(
        semantic_weight=0.0,
        confidence_weight=0.0,
        salience_weight=0.0,
        freshness_weight=0.0,
    )

    trace = policy.rank(
        owner_id="user:alice",
        query="q",
        candidates=[
            (_fact("lower", confidence=1.0, salience=1.0), 0.6),
            (_fact("higher", confidence=0.1, salience=0.1), 0.8),
        ],
        k=2,
        now=_NOW,
        ttl_days=0.0,
    )

    assert [hit.fact.fact_id for hit in trace.hits] == ["higher", "lower"]
