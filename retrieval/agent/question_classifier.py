"""问题分类器：识别问题类型，决定检索策略。

这是 Agentic RAG 的"第一层脑子"——不同问题类型用不同检索策略。

支持的问题类型：
- definition      定义类："什么是数据出境安全评估？"
- comparison      对比类："标准合同和安全评估有什么区别？"
- condition       条件判断类："我们需不需要申报安全评估？"
- process         流程类："安全评估的流程是什么？"
- case            案例分析类："A 公司把数据传给境外母公司，怎么办？"
- out_of_scope    领域外：与数据出境/个人信息保护/跨境合规无关（OOD 拦截）

每种类型对应不同的：
- 是否需要 decomposition
- query rewrite 策略
- 检索轮次预期
- 证据充分性门槛

关于 out_of_scope（OOD）：
    传统的 5 类是"封闭集分类"，面对"今天天气怎么样"这种领域外问题
    会被硬塞到 condition，然后浪费一次检索+证据检查才被兜底拒答。
    引入 out_of_scope 类作为 (N+1) 分类，在 Agent 入口即可短路。
"""

import logging
from dataclasses import dataclass
from typing import Optional

from config import settings
from retrieval.generation.chat_client import ChatClient, RETRYABLE_ERRORS

logger = logging.getLogger(__name__)


# 问题类型常量
QUESTION_TYPES = {
    "definition":    "定义解释类",
    "comparison":    "对比分析类",
    "condition":     "条件判断类",
    "process":       "流程操作类",
    "case":          "案例合规类",
    "out_of_scope":  "领域外问题",
}


@dataclass
class QuestionAnalysis:
    """问题分析结果——指导后续每一步该怎么做。"""
    question_type: str                    # 问题类型 key
    type_label: str                       # 中文标签
    needs_decomposition: bool             # 是否需要拆解子问题
    retrieval_strategy: str               # 检索策略: single / multi_query / decomposed / skip
    suggested_top_k: int                  # 建议的 top_k
    evidence_threshold: str               # 证据门槛: low / medium / high / n/a
    reasoning: str                        # 分类理由
    confidence: float = 0.8               # LLM 分类置信度 0~1（用于 OOD 软判决）

    @property
    def is_out_of_scope(self) -> bool:
        """是否为领域外问题（OOD），Agent 可据此提前短路。"""
        return self.question_type == "out_of_scope"

    @property
    def ood_decision(self) -> str:
        """
        OOD 软判决（双阈值）：
        - hard_refuse  : 高置信度 OOD，可直接短路（仍建议配合相似度校验）
        - probe        : 中置信度 OOD，走探针检索
        - downgrade    : 低置信度 OOD，降级为 condition 走正常流程
        - not_ood      : 非 OOD
        """
        if self.question_type != "out_of_scope":
            return "not_ood"
        if self.confidence >= 0.85:
            return "hard_refuse"
        if self.confidence >= 0.60:
            return "probe"
        return "downgrade"


CLASSIFY_PROMPT = """你是一个问题分析专家。本系统的领域是【数据出境 / 跨境合规 / 个人信息保护】相关法规问答。
请先判断问题是否属于本领域，若属于则进一步给出问题类型。

用户问题：{query}

候选类型：
0. out_of_scope — 与数据出境/跨境合规/个人信息保护【完全无关】的问题
                  明确包括但不限于：
                  - 天气、新闻、股票、交通、娱乐、日常闲聊
                  - 代码编写/调试（如"写段 Python"、"这个 bug 怎么修"）
                  - 菜谱/做饭/生活技巧（如"红烧肉怎么做"）
                  - 数学/翻译/其他通用任务
                  - 其他法规领域（如劳动法、税法、知识产权，且与数据无关时）
1. definition  — 定义解释类（问"什么是"、"定义"、"含义"等）
2. comparison  — 对比分析类（问"A和B有什么区别"、"异同"、"哪个"等）
3. condition   — 条件判断类（问"是否需要"、"满足什么条件"、"适不适用"等）
4. process     — 流程操作类（问"怎么做"、"流程是什么"、"步骤"等）
5. case        — 案例合规类（描述具体场景，问合规建议）

判断原则：
- 只要问题沾边"数据/个人信息/跨境/出境/传输/合规/监管"等方向，都应归入 1-5 类之一，不要轻易判为 out_of_scope
- 只有明显脱离本领域（如日常闲聊、其他技术领域）才判 out_of_scope
- 宁可放进来让下游证据检查兜底，也不要误杀用户正常问题

按以下格式输出一行（不要输出其他内容）：
类型|是否需要拆解(yes/no)|置信度(0~100)|理由(20字内)

置信度说明：
- 90~100：非常确定（典型的领域内问题 或 明显的闲聊/其他领域）
- 70~89 ：比较确定
- 50~69 ：不太确定（边界模糊、可能两可）
- <50   ：很不确定

示例：
condition|yes|92|涉及多个法规条件需要逐一检索
definition|no|88|单一概念直接检索即可
out_of_scope|no|95|明显闲聊，与数据出境无关
out_of_scope|no|92|代码编写请求，与法规领域无关
out_of_scope|no|90|菜谱问题，非本系统领域
out_of_scope|no|65|边界模糊，疑似偏离领域"""


# 每种类型的默认检索策略
TYPE_STRATEGIES = {
    "definition": {
        "retrieval_strategy": "single",       # 直接检索，一般一次够
        "suggested_top_k": 5,
        "evidence_threshold": "low",          # 找到定义就够了
    },
    "comparison": {
        "retrieval_strategy": "decomposed",   # 拆分后分别检索
        "suggested_top_k": 6,
        "evidence_threshold": "medium",       # 需要两边都有
    },
    "condition": {
        "retrieval_strategy": "multi_query",  # 多角度检索条件
        "suggested_top_k": 8,
        "evidence_threshold": "high",         # 需要完整条件链
    },
    "process": {
        "retrieval_strategy": "single",       # 通常一个文档就包含流程
        "suggested_top_k": 5,
        "evidence_threshold": "medium",
    },
    "case": {
        "retrieval_strategy": "decomposed",   # 拆出关键检索点
        "suggested_top_k": 8,
        "evidence_threshold": "high",         # 案例需要严谨
    },
    "out_of_scope": {
        "retrieval_strategy": "skip",         # OOD：跳过检索
        "suggested_top_k": 0,
        "evidence_threshold": "n/a",
    },
}


class QuestionClassifier:
    """问题分类器：识别问题类型，输出检索策略建议。"""

    def __init__(self):
        self.chat_client = ChatClient()

    def classify(self, query: str) -> QuestionAnalysis:
        """
        分析问题类型并返回检索策略建议。

        Args:
            query: 用户问题

        Returns:
            QuestionAnalysis 包含类型和策略建议
        """
        try:
            text = self.chat_client.complete(
                messages=[{
                    "role": "user",
                    "content": CLASSIFY_PROMPT.format(query=query),
                }],
                temperature=0.0,
                max_tokens=500,
            )
            return self._parse_classification(text, query)

        except Exception as e:
            logger.warning(f"问题分类失败，降级为 condition 类型: {e}")
            return self._default_analysis("condition", "分类器异常，降级处理")

    def _parse_classification(self, text: str, query: str) -> QuestionAnalysis:
        """解析 LLM 返回的分类结果。"""
        # 取第一个非空行
        line = ""
        for l in text.strip().split("\n"):
            l = l.strip()
            if l and "|" in l:
                line = l
                break

        if not line:
            return self._default_analysis("condition", f"解析失败: {text[:30]}")

        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            return self._default_analysis("condition", f"格式错误: {line[:30]}")

        q_type = parts[0].lower()
        needs_decomp = parts[1].lower() in ("yes", "是", "true") if len(parts) > 1 else False

        # 解析 confidence（第 3 位，可选；兼容老格式：若第 3 位不是数字就当作 reasoning）
        confidence = 0.8
        reasoning = ""
        if len(parts) >= 4:
            # 新格式: 类型|拆解|置信度|理由
            try:
                confidence = max(0.0, min(1.0, float(parts[2]) / 100.0))
            except ValueError:
                confidence = 0.8
            reasoning = parts[3]
        elif len(parts) == 3:
            # 老格式兼容: 类型|拆解|理由
            try:
                confidence = max(0.0, min(1.0, float(parts[2]) / 100.0))
            except ValueError:
                reasoning = parts[2]

        # 验证类型有效性
        if q_type not in TYPE_STRATEGIES:
            # 模糊匹配（注意：out_of_scope 优先匹配，避免被 "condition" 等词误匹）
            matched = None
            for key in ("out_of_scope", "definition", "comparison", "condition", "process", "case"):
                if key in q_type:
                    matched = key
                    break
            q_type = matched or "condition"

        strategy = TYPE_STRATEGIES[q_type]

        # 如果 LLM 说需要拆解，覆盖策略
        retrieval_strategy = strategy["retrieval_strategy"]
        if needs_decomp and retrieval_strategy == "single":
            retrieval_strategy = "decomposed"

        logger.info(
            f"问题分类: type={q_type}, decomp={needs_decomp}, "
            f"strategy={retrieval_strategy}, confidence={confidence:.2f}"
        )

        return QuestionAnalysis(
            question_type=q_type,
            type_label=QUESTION_TYPES.get(q_type, q_type),
            needs_decomposition=needs_decomp,
            retrieval_strategy=retrieval_strategy,
            suggested_top_k=strategy["suggested_top_k"],
            evidence_threshold=strategy["evidence_threshold"],
            reasoning=reasoning,
            confidence=confidence,
        )

    def _default_analysis(self, q_type: str, reasoning: str) -> QuestionAnalysis:
        """生成默认分析结果。"""
        strategy = TYPE_STRATEGIES[q_type]
        return QuestionAnalysis(
            question_type=q_type,
            type_label=QUESTION_TYPES.get(q_type, q_type),
            needs_decomposition=True,
            retrieval_strategy=strategy["retrieval_strategy"],
            suggested_top_k=strategy["suggested_top_k"],
            evidence_threshold=strategy["evidence_threshold"],
            reasoning=reasoning,
            confidence=0.3,   # 异常降级路径，置信度低
        )
