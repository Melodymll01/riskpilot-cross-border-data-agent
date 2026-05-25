"""证据充分性检查器（Agentic RAG 的"自我审查"能力）。

v1 只做简单的相关性评级（CORRECT/AMBIGUOUS/INCORRECT）。
v2 做的是 **证据充分性判断**：

1. 检索结果是不是都很泛？有没有命中核心法条？
2. 引用是否足够支撑结论？
3. 如果不够——具体缺什么？应该用什么关键词补充检索？
4. 如果补充也不够——应该拒答还是给保守结论？

核心区别：不只是说"够不够"，而是说"缺什么、怎么补"。
"""

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Any

from config import settings
from retrieval.generation.chat_client import ChatClient, RETRYABLE_ERRORS

logger = logging.getLogger(__name__)


class EvidenceVerdict(str, Enum):
    """证据充分性判定。"""
    SUFFICIENT = "sufficient"       # 证据充分，可以直接回答
    PARTIAL = "partial"             # 部分充分，需要补充特定方面
    INSUFFICIENT = "insufficient"   # 严重不足，需要大幅补充或拒答


@dataclass
class EvidenceCheckResult:
    """证据检查结果——不只评级，还告诉 Agent 下一步该怎么做。"""
    verdict: EvidenceVerdict
    confidence: float                                  # 0~1
    useful_docs: List[Dict[str, Any]]                  # 有用的文档（保留）
    irrelevant_docs: List[Dict[str, Any]]              # 无关的文档（丢弃）
    missing_aspects: List[str] = field(default_factory=list)   # 缺失的检索方面
    supplement_queries: List[str] = field(default_factory=list) # 建议的补充查询
    reasoning: str = ""                                # 判断理由
    should_refuse: bool = False                        # 是否建议拒答


EVIDENCE_CHECK_PROMPT = """你是一个严格的法规证据审查员。请判断以下检索结果是否足够回答用户的问题。

用户问题：{query}
问题类型：{question_type}
证据充分性要求：{evidence_threshold}

检索到的文档：
{doc_summaries}

请按以下 JSON 格式评估（不要输出其他内容）：
{{
  "verdict": "sufficient/partial/insufficient",
  "confidence": 0.0到1.0,
  "doc_useful": [有用的文档编号列表，如 [1, 3, 5]],
  "missing_aspects": ["缺失的方面1", "缺失的方面2"],
  "supplement_queries": ["建议的补充检索查询1", "建议的补充检索查询2"],
  "reasoning": "判断理由（80字内）",
  "should_refuse": false
}}

评估标准（{evidence_threshold}级别）：
- sufficient：现有文档能直接、完整地回答问题
- partial：找到了部分相关内容，但缺少关键方面（必须指出缺什么）
- insufficient：几乎没有找到直接相关内容

关键判断要点：
1. 有没有命中具体法条/规定？还是只有泛泛的概述？
2. 对于条件判断类问题：触发条件、数量门槛、例外情形是否都覆盖？
3. 对于案例类问题：能否支撑明确的合规结论？
4. 如果证据不足，must 在 supplement_queries 中给出补充检索建议
5. 如果你认为即使补充检索也难以回答（比如超出知识库范围），设 should_refuse=true"""


class EvidenceChecker:
    """证据充分性检查器：Agent 的"自我审查"能力。"""

    def __init__(self):
        self.chat_client = ChatClient()

    def check(
        self,
        query: str,
        results: List[Dict[str, Any]],
        question_type: str = "condition",
        evidence_threshold: str = "medium",
    ) -> EvidenceCheckResult:
        """
        检查证据是否足够回答问题。

        Args:
            query: 用户问题
            results: 检索到的文档列表
            question_type: 问题类型（影响判断标准）
            evidence_threshold: 证据门槛 low/medium/high

        Returns:
            EvidenceCheckResult 包含判定和下一步建议
        """
        if not results:
            return EvidenceCheckResult(
                verdict=EvidenceVerdict.INSUFFICIENT,
                confidence=0.0,
                useful_docs=[], irrelevant_docs=[],
                missing_aspects=["未检索到任何文档"],
                supplement_queries=[query],
                reasoning="未检索到任何文档",
                should_refuse=False,
            )

        # 构建文档摘要
        summaries = []
        for i, r in enumerate(results, 1):
            text = r.get("text", "")[:250]
            meta = r.get("metadata", {})
            dist = r.get("distance", "?")
            summaries.append(f"[{i}] (来源: {meta.get('source_name', '未知')}, 距离: {dist}) {text}")
        doc_summaries = "\n".join(summaries)

        threshold_desc = {"low": "低", "medium": "中", "high": "高"}.get(evidence_threshold, "中")

        try:
            text = self.chat_client.complete(
                messages=[{
                    "role": "user",
                    "content": EVIDENCE_CHECK_PROMPT.format(
                        query=query,
                        question_type=question_type,
                        evidence_threshold=threshold_desc,
                        doc_summaries=doc_summaries,
                    ),
                }],
                temperature=0.0,
                max_tokens=1500,
            )
            return self._parse_result(text, results)

        except Exception as e:
            logger.warning(f"证据检查失败，降级为 partial: {e}")
            return EvidenceCheckResult(
                verdict=EvidenceVerdict.PARTIAL,
                confidence=0.5,
                useful_docs=results,
                irrelevant_docs=[],
                reasoning=f"检查器异常: {str(e)[:40]}",
            )

    def _parse_result(
        self,
        text: str,
        results: List[Dict[str, Any]],
    ) -> EvidenceCheckResult:
        """解析 LLM 返回的评估 JSON。"""
        text = text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning(f"证据检查 JSON 解析失败: {text[:80]}")
            return EvidenceCheckResult(
                verdict=EvidenceVerdict.PARTIAL,
                confidence=0.5,
                useful_docs=results,
                irrelevant_docs=[],
                reasoning="JSON 解析失败，降级处理",
            )

        # 解析 verdict
        verdict_str = data.get("verdict", "partial").lower()
        verdict_map = {v.value: v for v in EvidenceVerdict}
        verdict = verdict_map.get(verdict_str, EvidenceVerdict.PARTIAL)

        # 按文档编号分类
        useful_indices = set(data.get("doc_useful", []))
        useful_docs = []
        irrelevant_docs = []
        for i, r in enumerate(results, 1):
            if i in useful_indices or not useful_indices:
                useful_docs.append(r)
            else:
                irrelevant_docs.append(r)

        result = EvidenceCheckResult(
            verdict=verdict,
            confidence=float(data.get("confidence", 0.5)),
            useful_docs=useful_docs,
            irrelevant_docs=irrelevant_docs,
            missing_aspects=data.get("missing_aspects", []),
            supplement_queries=data.get("supplement_queries", []),
            reasoning=data.get("reasoning", ""),
            should_refuse=bool(data.get("should_refuse", False)),
        )

        logger.info(
            f"证据检查: verdict={result.verdict.value}, "
            f"confidence={result.confidence:.2f}, "
            f"useful={len(result.useful_docs)}/{len(results)}, "
            f"missing={result.missing_aspects}, "
            f"refuse={result.should_refuse}"
        )
        return result
