"""Reranker 模块：定义重排序接口和默认实现。

当前提供 PassthroughReranker（直通，不做重排序），
后续可实现基于 cross-encoder 或 API 的重排序策略。
"""

import logging
from abc import ABC, abstractmethod
from typing import List, Dict, Any

from config import settings

logger = logging.getLogger(__name__)


class BaseReranker(ABC):
    """重排序器基类，定义统一接口。"""

    @abstractmethod
    def      rerank(self, query: str, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        对检索结果进行重排序。

        Args:
            query: 用户查询
            results: 初步检索结果列表

        Returns:
            重排序后的结果列表
        """
        ...


class PassthroughReranker(BaseReranker):
    """直通重排序器：不做任何重排序，直接返回原始结果。"""

    def rerank(self, query: str, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        logger.debug("使用 PassthroughReranker，跳过重排序")
        return results


class DistanceThresholdReranker(BaseReranker):
    """基于余弦距离的简单过滤器，剔除相关度过低的结果。"""

    def __init__(self, max_distance: float = settings.distance_threshold):
        self.max_distance = max_distance

    def rerank(self, query: str, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not results:
            return results

        filtered = []
        for item in results:
            dist = item.get("distance")
            # distance 越小越相似，超过阈值则丢弃
            if dist is not None and dist > self.max_distance:
                continue
            filtered.append(item)

        # Chroma 已按距离排序，这里保持顺序
        logger.info(
            "重排序后保留 %s/%s (max_distance=%.3f)",
            len(filtered),
            len(results),
            self.max_distance,
        )
        return filtered

# ---- Cross-Encoder 重排序 ----

class CrossEncoderReranker(BaseReranker):
    """基于 Cross-Encoder 模型的重排序器。

    Cross-Encoder 对 (query, document) 对做联合编码，
    比 bi-encoder 的余弦距离更精确，适合在 top-K 候选集上做精排。

    支持 GPU 推理（自动检测 CUDA），支持按分数阈值过滤低相关结果。
    """

    def __init__(
        self,
        model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
        device: str = "auto",
        score_threshold: float | None = None,
    ):
        # 离线模式由环境变量控制（默认在线，依赖 HF_ENDPOINT 镜像加速）。
        # 已下载过模型的环境可在 .env 中显式设置：
        #   HF_HUB_OFFLINE=1
        #   TRANSFORMERS_OFFLINE=1
        # 这样可避免每次启动都向 HuggingFace 发 HEAD 请求。
        import os
        _offline = os.environ.get("HF_HUB_OFFLINE") == "1"

        from sentence_transformers import CrossEncoder

        # 设备选择：auto → 优先 GPU
        if device == "auto":
            try:
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:
                device = "cpu"

        logger.info(
            f"加载 Cross-Encoder 模型: {model_name} (device={device}, offline={_offline})"
        )
        self.model = CrossEncoder(model_name, device=device)
        self.device = device
        self.score_threshold = score_threshold

    def rerank(self, query: str, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not results:
            return results

        pairs = [(query, r.get("text", "")) for r in results]
        scores = self.model.predict(pairs)

        for r, s in zip(results, scores):
            r["rerank_score"] = float(s)

        # 按 rerank_score 降序排序
        results.sort(key=lambda x: x["rerank_score"], reverse=True)

        # 分数阈值过滤（可选）
        before = len(results)
        if self.score_threshold is not None:
            results = [r for r in results if r["rerank_score"] >= self.score_threshold]

        logger.info(
            "Cross-Encoder 重排序: %d → %d 条 (device=%s, 分数范围 [%.3f, %.3f]%s)",
            before,
            len(results),
            self.device,
            min(scores) if len(scores) else 0.0,
            max(scores) if len(scores) else 0.0,
            f", threshold={self.score_threshold}" if self.score_threshold is not None else "",
        )
        return results
