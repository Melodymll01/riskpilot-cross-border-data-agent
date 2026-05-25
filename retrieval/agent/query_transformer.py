"""查询变换模块（Agentic RAG 核心组件）。

与 v1 的区别：v1 对所有问题无脑做 拆解+改写+HyDE 三步固定流程。
v2 根据问题分类器的判断，选择性地执行不同策略：

- definition  → 只做术语改写（把口语变法规用语），不拆解
- comparison  → 拆出对比对象，分别生成查询
- condition   → 多角度改写（法条角度、门槛角度、例外角度）
- process     → 改写为流程/步骤相关的检索表达
- case        → 拆出关键法律要素（什么数据、什么场景、什么主体），分别查
"""

import logging
from dataclasses import dataclass, field
from typing import List

from config import settings
from retrieval.generation.chat_client import ChatClient, RETRYABLE_ERRORS

logger = logging.getLogger(__name__)


@dataclass
class TransformedQuery:
    """查询变换结果。"""
    original: str
    rewritten_queries: List[str] = field(default_factory=list)
    sub_questions: List[str] = field(default_factory=list)
    strategy_used: str = ""

    @property
    def all_queries(self) -> List[str]:
        """返回所有去重后的检索查询（原始 + 改写 + 子问题）。"""
        seen = set()
        result = []
        for q in [self.original] + self.rewritten_queries + self.sub_questions:
            q_stripped = q.strip()
            if q_stripped and q_stripped not in seen:
                seen.add(q_stripped)
                result.append(q_stripped)
        return result


# ── 不同类型的 Prompt 策略 ──────────────────────────────────

REWRITE_DEFINITION = """你是数据出境法规检索专家。用户问了一个定义类问题，请把它改写成更适合在法规知识库中检索的表达。

要求：
1. 把口语化表达转为法规文本中的标准术语
2. 生成 1~2 个改写查询
3. 每条独占一行，不要编号

用户问题：{query}"""

REWRITE_COMPARISON = """你是数据出境法规检索专家。用户问了一个对比类问题，请把它拆分为分别检索各个对比对象的查询。

要求：
1. 识别要对比的对象（如 标准合同 vs 安全评估）
2. 为每个对比对象各生成一条检索查询
3. 再生成一条同时涵盖两者区别的查询
4. 每条独占一行，不要编号

用户问题：{query}"""

REWRITE_CONDITION = """你是数据出境法规检索专家。用户问了一个条件判断类问题，请从多个角度改写成适合知识库检索的查询。

要求：
1. 第一条：用法规标准术语改写核心问题
2. 第二条：从触发条件/门槛的角度生成查询
3. 第三条：从例外情形/豁免条件的角度生成查询
4. 每条独占一行，不要编号

用户问题：{query}"""

REWRITE_PROCESS = """你是数据出境法规检索专家。用户问了一个流程操作类问题，请改写成更适合检索流程性文档的表达。

要求：
1. 改写为包含"流程""步骤""程序""办理"等操作性关键词的查询
2. 生成 1~2 个改写查询
3. 每条独占一行，不要编号

用户问题：{query}"""

REWRITE_CASE = """你是数据出境法规专家。用户描述了一个具体场景，需要合规判断。请把场景拆分为若干关键法律要素，每个要素生成一条检索查询。

要求：
1. 提取关键要素：涉及什么数据类型、什么传输场景、什么主体、适用什么法规
2. 每个要素对应一条检索查询
3. 最后加一条综合性的检索查询
4. 最多 4 条，每条独占一行，不要编号

用户场景：{query}"""

DECOMPOSE_PROMPT = """你是问题拆解专家。请把这个复杂问题拆分为 2~4 个可独立检索的子问题。

规则：
1. 每个子问题应能独立回答，并最终组合成完整答案
2. 子问题要使用知识库中可能出现的专业术语
3. 每条独占一行，不要编号

原始问题：{query}"""

# 类型 → prompt 映射
TYPE_PROMPTS = {
    "definition":  REWRITE_DEFINITION,
    "comparison":  REWRITE_COMPARISON,
    "condition":   REWRITE_CONDITION,
    "process":     REWRITE_PROCESS,
    "case":        REWRITE_CASE,
}


class QueryTransformer:
    """查询变换器：根据问题类型选择性执行不同改写策略。"""

    def __init__(self):
        self.chat_client = ChatClient()

    def transform(
        self,
        query: str,
        question_type: str = "condition",
        needs_decomposition: bool = False,
    ) -> TransformedQuery:
        """
        根据问题类型做针对性的查询变换。

        Args:
            query: 用户原始问题
            question_type: 问题类型（由 QuestionClassifier 提供）
            needs_decomposition: 是否需要拆解子问题

        Returns:
            TransformedQuery
        """
        result = TransformedQuery(original=query, strategy_used=question_type)

        # 1. 按类型做针对性改写
        result.rewritten_queries = self._type_aware_rewrite(query, question_type)

        # 2. 如果分类器判定需要拆解，才做 decomposition
        if needs_decomposition:
            result.sub_questions = self._decompose(query)

        logger.info(
            f"查询变换[{question_type}]: "
            f"改写={len(result.rewritten_queries)}, "
            f"子问题={len(result.sub_questions)}, "
            f"总查询={len(result.all_queries)}"
        )
        return result

    def _type_aware_rewrite(self, query: str, question_type: str) -> List[str]:
        """根据问题类型选择对应 prompt 做改写。"""
        prompt_template = TYPE_PROMPTS.get(question_type, REWRITE_CONDITION)
        try:
            text = self.chat_client.complete(
                messages=[{
                    "role": "user",
                    "content": prompt_template.format(query=query),
                }],
                temperature=0.0,
                max_tokens=1000,
            )
            queries = [q.strip() for q in text.split("\n") if q.strip()]
            return queries[:4]
        except Exception as e:
            logger.warning(f"查询改写失败: {e}")
            return []

    def _decompose(self, query: str) -> List[str]:
        """问题拆解：将复杂问题拆分为可独立检索的子问题。"""
        try:
            text = self.chat_client.complete(
                messages=[{
                    "role": "user",
                    "content": DECOMPOSE_PROMPT.format(query=query),
                }],
                temperature=0.0,
                max_tokens=1200,
            )
            questions = [q.strip() for q in text.split("\n") if q.strip()]
            if len(questions) == 1 and questions[0] == query:
                return []
            return questions[:4]
        except Exception as e:
            logger.warning(f"问题拆解失败: {e}")
            return []
