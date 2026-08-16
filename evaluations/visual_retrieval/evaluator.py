"""小规模图片召回数据生成与指标计算。"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class EvalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VisualQuery(EvalModel):
    query_id: str
    query: str
    relevant_asset_ids: list[str] = Field(min_length=1)


class VisualDataset(EvalModel):
    name: str
    version: str
    synthetic: bool = True
    images_dir: str
    queries: list[VisualQuery] = Field(min_length=1)
    recall_at_1_min: float = Field(default=0.75, ge=0.0, le=1.0)
    recall_at_3_min: float = Field(default=0.9, ge=0.0, le=1.0)


def load_dataset(path: str | Path) -> VisualDataset:
    return VisualDataset.model_validate_json(Path(path).read_text(encoding="utf-8"))


def evaluate_rankings(
    dataset: VisualDataset,
    rankings: dict[str, list[str]],
) -> dict[str, object]:
    hits_at_1 = 0
    hits_at_3 = 0
    cases: list[dict[str, object]] = []
    for query in dataset.queries:
        ranked = rankings.get(query.query_id, [])
        relevant = set(query.relevant_asset_ids)
        hit_at_1 = bool(set(ranked[:1]) & relevant)
        hit_at_3 = bool(set(ranked[:3]) & relevant)
        hits_at_1 += int(hit_at_1)
        hits_at_3 += int(hit_at_3)
        cases.append(
            {
                "query_id": query.query_id,
                "query": query.query,
                "ranked_asset_ids": ranked,
                "hit_at_1": hit_at_1,
                "hit_at_3": hit_at_3,
            }
        )
    count = len(dataset.queries)
    recall_at_1 = hits_at_1 / count
    recall_at_3 = hits_at_3 / count
    return {
        "dataset": {
            "name": dataset.name,
            "version": dataset.version,
            "query_count": count,
            "synthetic": dataset.synthetic,
        },
        "metrics": {
            "recall_at_1": recall_at_1,
            "recall_at_3": recall_at_3,
        },
        "gates": {
            "recall_at_1": {
                "value": recall_at_1,
                "threshold": dataset.recall_at_1_min,
                "passed": recall_at_1 >= dataset.recall_at_1_min,
            },
            "recall_at_3": {
                "value": recall_at_3,
                "threshold": dataset.recall_at_3_min,
                "passed": recall_at_3 >= dataset.recall_at_3_min,
            },
        },
        "passed": (
            recall_at_1 >= dataset.recall_at_1_min
            and recall_at_3 >= dataset.recall_at_3_min
        ),
        "cases": cases,
    }


def write_dataset(path: str | Path, dataset: VisualDataset) -> None:
    Path(path).write_text(
        json.dumps(dataset.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
