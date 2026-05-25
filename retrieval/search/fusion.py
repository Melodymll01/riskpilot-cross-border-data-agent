"""Reciprocal Rank Fusion (RRF)：多路召回排名融合。

公式：score(d) = Σ_i  1 / (k + rank_i(d))
    - k 通常取 60（Cormack 2009 原论文）
    - rank 从 1 开始；某路未命中则不贡献

本项目用于融合向量检索和 BM25 两路。
"""

import logging
from typing import List, Dict, Any, Sequence

logger = logging.getLogger(__name__)

DEFAULT_RRF_K = 60


def rrf_fuse(
    rankings: Sequence[Sequence[Dict[str, Any]]],
    k: int = DEFAULT_RRF_K,
    weights: Sequence[float] = None,
) -> List[Dict[str, Any]]:
    """
    对多路检索结果做 RRF 融合。

    Args:
        rankings: 多路召回结果，每路是按排名排序的 list[dict]，dict 必须含 "id"
        k: RRF 平滑常数，默认 60
        weights: 可选权重，默认每路权重 1.0

    Returns:
        融合后按 rrf_score 降序的 list[dict]，保留首次出现文档的原始字段，
        并附加 rrf_score 和 fused_from 字段。
    """
    if not rankings:
        return []
    if weights is None:
        weights = [1.0] * len(rankings)
    assert len(weights) == len(rankings), "weights 长度必须与 rankings 一致"

    score_map: Dict[str, float] = {}
    doc_map: Dict[str, Dict[str, Any]] = {}
    source_map: Dict[str, List[str]] = {}

    for path_idx, ranking in enumerate(rankings):
        w = weights[path_idx]
        for rank, item in enumerate(ranking, start=1):
            doc_id = item.get("id")
            if not doc_id:
                continue
            contrib = w * 1.0 / (k + rank)
            score_map[doc_id] = score_map.get(doc_id, 0.0) + contrib
            # 首次出现时保存原始文档（后续路径只累加分数）
            if doc_id not in doc_map:
                doc_map[doc_id] = dict(item)
                source_map[doc_id] = []
            source_map[doc_id].append(item.get("match_type") or f"path_{path_idx}")

    fused: List[Dict[str, Any]] = []
    for doc_id, score in score_map.items():
        doc = doc_map[doc_id]
        doc["rrf_score"] = score
        doc["fused_from"] = source_map[doc_id]
        fused.append(doc)

    fused.sort(key=lambda x: x["rrf_score"], reverse=True)
    logger.debug(
        f"RRF 融合: {len(rankings)} 路 → {len(fused)} 条唯一文档 (k={k})"
    )
    return fused
