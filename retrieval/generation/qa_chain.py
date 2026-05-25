"""问答生成模块：基于检索结果调用 LLM 生成回答，附带引用来源。"""

import logging
import time
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

from config import settings
from retrieval.generation.chat_client import ChatClient, RETRYABLE_ERRORS

logger = logging.getLogger(__name__)

MAX_RETRIES = 3

# 系统提示词：指引 LLM 基于上下文回答
SYSTEM_PROMPT = """你是一个专业的数据出境法规知识库问答助手。

**核心规则：**
1. 你必须严格基于下方提供的【参考资料】来回答用户的问题，不得使用训练数据中的知识。
2. 如果参考资料中没有相关信息，请明确回复"根据现有知识库内容，暂时无法回答该问题"，绝不编造。
3. 如果参考资料只能部分回答问题，先回答能回答的部分，再明确说明哪些内容知识库中暂无涉及。
4. 回答时请在关键论述后标注具体的参考资料编号（如 [1]、[2]），以便用户溯源。
5. 回答要准确、专业、条理清晰，使用中文。

**回答格式：**
- 先给出完整回答（在关键论述后用 [编号] 标注引用来源）
- 如果涉及多个要点，请用编号列表组织
- 在回答末尾用"**参考来源：**[1][2]..."汇总引用了哪些资料

**置信度判断：**
- 若参考资料高度相关且充分，请正常回答
- 若参考资料仅部分相关，请在回答前加注"⚠️ 以下回答基于有限的相关内容"
- 若参考资料完全不相关，请直接拒答
"""

# 生成 context 模板 — 精简格式减少 token 浪费
CONTEXT_TEMPLATE = """【{idx}】[{source_type} | {source_name}]
{text}
"""


@dataclass
class Citation:
    """引用来源信息。"""
    source_type: str
    source_name: str
    title: str
    source_url: Optional[str]
    text_snippet: str  # 原文片段


@dataclass
class QAResult:
    """问答结果。"""
    answer: str
    citations: List[Citation]
    has_enough_context: bool


class QAChain:
    """基于检索结果的问答生成链。"""

    def __init__(self):
        self.chat_client = ChatClient()
        self.temperature = settings.chat_temperature
        self.max_tokens = settings.chat_max_tokens

    def generate(
        self,
        query: str,
        retrieved_results: List[Dict[str, Any]],
    ) -> QAResult:
        """
        基于检索结果生成回答。

        Args:
            query: 用户问题
            retrieved_results: 检索到的 chunk 列表

        Returns:
            QAResult 包含回答、引用和是否有足够上下文
        """
        # 如果没有检索到任何结果，直接拒答
        if not retrieved_results:
            logger.warning("未检索到任何相关内容，返回拒答")
            return QAResult(
                answer="抱歉，根据现有知识库内容，暂时无法回答该问题。请尝试导入更多相关文档或调整提问方式。",
                citations=[],
                has_enough_context=False,
            )

        # 构建上下文
        context_parts = []
        citations = []

        for idx, result in enumerate(retrieved_results, 1):
            meta = result.get("metadata", {})

            context_part = CONTEXT_TEMPLATE.format(
                idx=idx,
                source_type="文件" if meta.get("source_type") == "file" else "网页",
                source_name=meta.get("source_name", "未知"),
                text=result.get("text", ""),
            )
            context_parts.append(context_part)

            # 构建引用对象 —— 优先使用原始命中文本（上下文扩展前的）
            snippet = result.get("original_text") or result.get("text", "")
            if len(snippet) > 200:
                snippet = snippet[:200] + "..."

            citations.append(Citation(
                source_type=meta.get("source_type", "unknown"),
                source_name=meta.get("source_name", "未知"),
                title=meta.get("title", ""),
                source_url=meta.get("source_url"),
                text_snippet=snippet,
            ))

        context = "\n".join(context_parts)

        # 构建用户消息
        user_message = f"""请根据以下参考资料回答问题。

{context}

**用户问题：** {query}
"""

        logger.info(f"正在调用 LLM 生成回答，上下文包含 {len(retrieved_results)} 条参考资料")

        # 调用 LLM（带重试）
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]
        answer = self._chat_with_retry(messages)

        logger.info("LLM 回答生成完成")

        return QAResult(
            answer=answer,
            citations=citations,
            has_enough_context=True,
        )

    def generate_stream(
        self,
        query: str,
        retrieved_results: List[Dict[str, Any]],
    ):
        """流式生成回答，yield 文本片段。最终 yield 一个 QAResult 对象（包含引用）。

        Yields:
            str: 文本片段
            QAResult: 最后一个 yield，包含完整引用信息
        """
        if not retrieved_results:
            logger.warning("未检索到任何相关内容，返回拒答")
            answer = "抱歉，根据现有知识库内容，暂时无法回答该问题。请尝试导入更多相关文档或调整提问方式。"
            yield answer
            yield QAResult(answer=answer, citations=[], has_enough_context=False)
            return

        # 构建上下文和引用（与 generate 方法相同）
        context_parts = []
        citations = []
        for idx, result in enumerate(retrieved_results, 1):
            meta = result.get("metadata", {})
            context_part = CONTEXT_TEMPLATE.format(
                idx=idx,
                source_type="文件" if meta.get("source_type") == "file" else "网页",
                source_name=meta.get("source_name", "未知"),
                text=result.get("text", ""),
            )
            context_parts.append(context_part)
            snippet = result.get("original_text") or result.get("text", "")
            if len(snippet) > 200:
                snippet = snippet[:200] + "..."
            citations.append(Citation(
                source_type=meta.get("source_type", "unknown"),
                source_name=meta.get("source_name", "未知"),
                title=meta.get("title", ""),
                source_url=meta.get("source_url"),
                text_snippet=snippet,
            ))

        context = "\n".join(context_parts)
        user_message = f"""请根据以下参考资料回答问题。

{context}

**用户问题：** {query}
"""
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]

        logger.info(f"正在流式调用 LLM，上下文包含 {len(retrieved_results)} 条参考资料")

        full_answer = []
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                for text_chunk in self.chat_client.complete_stream(
                    messages=messages,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                ):
                    full_answer.append(text_chunk)
                    yield text_chunk
                break
            except RETRYABLE_ERRORS as e:
                if attempt == MAX_RETRIES:
                    raise
                wait = 2 ** attempt
                logger.warning(f"LLM 流式调用失败 (第{attempt}次)，{wait}s 后重试: {e}")
                time.sleep(wait)
                full_answer.clear()

        logger.info("LLM 流式回答生成完成")
        yield QAResult(
            answer="".join(full_answer),
            citations=citations,
            has_enough_context=True,
        )

    def _chat_with_retry(self, messages: list) -> str:
        """调用 Chat API，失败时自动重试。"""
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                return self.chat_client.complete(
                    messages=messages,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                )
            except RETRYABLE_ERRORS as e:
                if attempt == MAX_RETRIES:
                    raise
                wait = 2 ** attempt
                logger.warning(f"LLM API 调用失败 (第{attempt}次)，{wait}s 后重试: {e}")
                time.sleep(wait)
