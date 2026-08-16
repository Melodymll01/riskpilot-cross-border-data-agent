"""AI 长期记忆召回确定性协议评测器。"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from domain.memory import MemoryRecallPolicy
from domain.models import Fact


class EvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MemoryRecallFact(EvaluationModel):
    fact_id: str = Field(min_length=1, max_length=100)
    owner_id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=1000)
    semantic_score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    salience: float = Field(default=0.5, ge=0.0, le=1.0)
    age_days: float = Field(default=0.0, ge=0.0)
    superseded_by: str | None = None


class MemoryRecallCase(EvaluationModel):
    case_id: str = Field(min_length=1, max_length=100)
    category: str = Field(min_length=1, max_length=100)
    owner_id: str = Field(min_length=1, max_length=200)
    query: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=3, ge=1, le=20)
    ttl_days: float = Field(default=365.0, ge=0.0)
    candidates: list[MemoryRecallFact]
    relevant_fact_ids: list[str] = Field(default_factory=list)
    forbidden_fact_ids: list[str] = Field(default_factory=list)
    expected_top_fact_id: str | None = None

    @model_validator(mode="after")
    def validate_case(self) -> MemoryRecallCase:
        fact_ids = [candidate.fact_id for candidate in self.candidates]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError(f"{self.case_id}: fact_id 不能重复")
        known = set(fact_ids)
        if not set(self.relevant_fact_ids).issubset(known):
            raise ValueError(f"{self.case_id}: relevant_fact_ids 引用了未知事实")
        if not set(self.forbidden_fact_ids).issubset(known):
            raise ValueError(f"{self.case_id}: forbidden_fact_ids 引用了未知事实")
        if self.expected_top_fact_id is not None and self.expected_top_fact_id not in known:
            raise ValueError(f"{self.case_id}: expected_top_fact_id 引用了未知事实")
        return self


class MemoryRecallThresholds(EvaluationModel):
    hit_rate_at_k_min: float = Field(ge=0.0, le=1.0)
    top1_accuracy_min: float = Field(ge=0.0, le=1.0)
    forbidden_recall_count_max: int = Field(ge=0)
    irrelevant_recall_rate_max: float = Field(ge=0.0, le=1.0)


class MemoryRecallDataset(EvaluationModel):
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=50)
    description: str = Field(min_length=1)
    usage: str = Field(min_length=1)
    thresholds: MemoryRecallThresholds
    cases: list[MemoryRecallCase] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_cases(self) -> MemoryRecallDataset:
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("case_id 不能重复")
        return self


def load_dataset(path: str | Path) -> MemoryRecallDataset:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("评测数据集根节点必须是 JSON 对象")
    return MemoryRecallDataset.model_validate(data)


def evaluate_protocol(
    dataset: MemoryRecallDataset,
    *,
    policy: MemoryRecallPolicy | None = None,
) -> dict[str, object]:
    recall_policy = policy or MemoryRecallPolicy()
    now = 2_000_000_000.0
    case_results: list[dict[str, object]] = []
    cases_with_relevant = 0
    hit_cases = 0
    top1_cases = 0
    top1_matches = 0
    forbidden_recall_count = 0
    recalled_count = 0
    irrelevant_recall_count = 0

    for case in dataset.cases:
        candidates = [
            (
                Fact(
                    fact_id=candidate.fact_id,
                    owner_id=candidate.owner_id,
                    text=candidate.text,
                    confidence=candidate.confidence,
                    salience=candidate.salience,
                    created_at=now - candidate.age_days * 86400.0,
                    last_used_at=now - candidate.age_days * 86400.0,
                    superseded_by=candidate.superseded_by,
                ),
                candidate.semantic_score,
            )
            for candidate in case.candidates
        ]
        trace = recall_policy.rank(
            owner_id=case.owner_id,
            query=case.query,
            candidates=candidates,
            k=case.k,
            now=now,
            ttl_days=case.ttl_days,
        )
        recalled_ids = [hit.fact.fact_id for hit in trace.hits]
        relevant = set(case.relevant_fact_ids)
        forbidden = set(case.forbidden_fact_ids)
        hit = bool(relevant.intersection(recalled_ids))
        if relevant:
            cases_with_relevant += 1
            hit_cases += int(hit)
        top1_correct: bool | None = None
        if case.expected_top_fact_id is not None:
            top1_cases += 1
            top1_correct = bool(recalled_ids) and (recalled_ids[0] == case.expected_top_fact_id)
            top1_matches += int(top1_correct)

        forbidden_hits = [fact_id for fact_id in recalled_ids if fact_id in forbidden]
        irrelevant_hits = [
            fact_id
            for fact_id in recalled_ids
            if fact_id not in relevant and fact_id not in forbidden
        ]
        forbidden_recall_count += len(forbidden_hits)
        irrelevant_recall_count += len(irrelevant_hits)
        recalled_count += len(recalled_ids)
        case_results.append(
            {
                "case_id": case.case_id,
                "category": case.category,
                "recalled_fact_ids": recalled_ids,
                "hit": hit if relevant else None,
                "top1_correct": top1_correct,
                "forbidden_hits": forbidden_hits,
                "irrelevant_hits": irrelevant_hits,
                "rejected_counts": trace.rejected_counts,
            }
        )

    hit_rate_at_k = hit_cases / cases_with_relevant if cases_with_relevant else 1.0
    top1_accuracy = top1_matches / top1_cases if top1_cases else 1.0
    irrelevant_recall_rate = irrelevant_recall_count / recalled_count if recalled_count else 0.0
    thresholds = dataset.thresholds
    gates = {
        "hit_rate_at_k": {
            "value": hit_rate_at_k,
            "threshold": thresholds.hit_rate_at_k_min,
            "passed": hit_rate_at_k >= thresholds.hit_rate_at_k_min,
        },
        "top1_accuracy": {
            "value": top1_accuracy,
            "threshold": thresholds.top1_accuracy_min,
            "passed": top1_accuracy >= thresholds.top1_accuracy_min,
        },
        "forbidden_recall_count": {
            "value": forbidden_recall_count,
            "threshold": thresholds.forbidden_recall_count_max,
            "passed": forbidden_recall_count <= thresholds.forbidden_recall_count_max,
        },
        "irrelevant_recall_rate": {
            "value": irrelevant_recall_rate,
            "threshold": thresholds.irrelevant_recall_rate_max,
            "passed": irrelevant_recall_rate <= thresholds.irrelevant_recall_rate_max,
        },
    }
    return {
        "dataset": {
            "name": dataset.name,
            "version": dataset.version,
            "case_count": len(dataset.cases),
        },
        "mode": "protocol_self_check",
        "strategy_version": recall_policy.strategy_version,
        "production_embedding_evidence": False,
        "metrics": {
            "hit_rate_at_k": hit_rate_at_k,
            "top1_accuracy": top1_accuracy,
            "forbidden_recall_count": forbidden_recall_count,
            "irrelevant_recall_rate": irrelevant_recall_rate,
        },
        "gates": gates,
        "passed": all(gate["passed"] for gate in gates.values()),
        "cases": case_results,
    }
