"""深度报告生成器（Agentic RAG 长文生成组件）。

与普通 QA 链不同，报告生成器：
1. 按主题组织内容，生成结构化的深度报告
2. 每个论点都带有精确的引用标签 [来源X]
3. 支持多轮迭代：先生成大纲，再逐段填充
4. 最高可生成 3000+ 字的深度分析报告
"""

import logging
import time
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Generator

from config import settings
from retrieval.generation.chat_client import ChatClient, RETRYABLE_ERRORS

logger = logging.getLogger(__name__)

MAX_RETRIES = 3

# ── Prompt 模板 ──────────────────────────────────────────────

OUTLINE_PROMPT = """你是一个专业的数据出境法规分析师。请根据用户问题和参考资料，生成一份深度分析报告的大纲。

用户问题：{query}

参考资料摘要：
{context_summary}

要求：
1. 大纲包含 3~6 个主要章节
2. 每个章节标题简洁明确
3. 每行一个章节标题，不要编号，不要子标题
4. 直接输出大纲，不要解释
"""

REPORT_PROMPT = """你是一个专业的数据出境法规分析师。请根据参考资料撰写一份深度分析报告。

**用户问题：** {query}

**参考资料：**
{context}

**报告要求：**
1. 标题：用一级标题概括报告主题
2. 摘要：用 2-3 句话概括核心结论
3. 正文：按主题分章节论述，每个章节用二级标题
4. **引用标注**：在每个关键论点后标注引用来源，格式为 [来源X]，X 对应参考资料的编号
5. 结论：总结核心要点，给出明确的合规建议
6. 参考来源：在报告末尾列出所有引用的来源
7. 报告长度：1500~3000 字
8. 语言：专业、严谨、条理清晰

**格式示例：**
# 报告标题

## 摘要
...

## 一、第一个主题
根据相关法规规定...[来源1]...[来源2]

## 二、第二个主题
...

## 结论与建议
...

**参考来源：**
- [来源1] 来源名称
- [来源2] 来源名称
"""

CONTEXT_TEMPLATE = """【来源{idx}】[{source_type} | {source_name}]
{text}
"""


@dataclass
class ReportCitation:
    """报告引用项。"""
    index: int
    source_type: str
    source_name: str
    title: str
    source_url: Optional[str]
    text_snippet: str


@dataclass
class DeepReport:
    """深度报告结果。"""
    title: str
    content: str                            # Markdown 格式的完整报告
    citations: List[ReportCitation]
    query: str                              # 原始问题
    source_count: int                       # 参考来源数
    word_count: int                         # 报告字数
    retrieval_rounds: int = 1               # 检索轮次
    web_search_used: bool = False           # 是否使用了联网搜索


class ReportGenerator:
    """深度报告生成器。"""

    def __init__(self):
        self.chat_client = ChatClient()

    def generate(
        self,
        query: str,
        retrieved_results: List[Dict[str, Any]],
        retrieval_rounds: int = 1,
        web_search_used: bool = False,
    ) -> DeepReport:
        """
        生成深度报告（非流式）。

        Args:
            query: 用户问题
            retrieved_results: 检索到的所有文档（可能来自多轮检索 + 联网搜索）
            retrieval_rounds: 检索轮次（用于报告元数据）
            web_search_used: 是否使用了联网搜索

        Returns:
            DeepReport 结构化报告
        """
        if not retrieved_results:
            return DeepReport(
                title="无法生成报告",
                content="抱歉，未检索到相关知识内容，无法生成深度报告。请先导入相关文档。",
                citations=[],
                query=query,
                source_count=0,
                word_count=0,
            )

        context, citations = self._build_context(retrieved_results)

        messages = [
            {"role": "system", "content": "你是一个专业的数据出境法规分析师，擅长撰写深度合规分析报告。"},
            {"role": "user", "content": REPORT_PROMPT.format(query=query, context=context)},
        ]

        content = self._chat_with_retry(messages, max_tokens=3000)

        # 提取标题（第一个 # 开头的行）
        title = query
        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("# ") and not line.startswith("## "):
                title = line.lstrip("# ").strip()
                break

        return DeepReport(
            title=title,
            content=content,
            citations=citations,
            query=query,
            source_count=len(retrieved_results),
            word_count=len(content),
            retrieval_rounds=retrieval_rounds,
            web_search_used=web_search_used,
        )

    def generate_stream(
        self,
        query: str,
        retrieved_results: List[Dict[str, Any]],
        retrieval_rounds: int = 1,
        web_search_used: bool = False,
    ) -> Generator:
        """
        流式生成深度报告。

        Yields:
            str: 文本片段
            DeepReport: 最后一个 yield，包含完整报告信息
        """
        if not retrieved_results:
            msg = "抱歉，未检索到相关知识内容，无法生成深度报告。请先导入相关文档。"
            yield msg
            yield DeepReport(
                title="无法生成报告", content=msg, citations=[],
                query=query, source_count=0, word_count=0,
            )
            return

        context, citations = self._build_context(retrieved_results)

        messages = [
            {"role": "system", "content": "你是一个专业的数据出境法规分析师，擅长撰写深度合规分析报告。"},
            {"role": "user", "content": REPORT_PROMPT.format(query=query, context=context)},
        ]

        full_content = []
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                for text_chunk in self.chat_client.complete_stream(
                    messages=messages,
                    temperature=0.3,
                    max_tokens=3000,
                ):
                    full_content.append(text_chunk)
                    yield text_chunk
                break
            except RETRYABLE_ERRORS as e:
                if attempt == MAX_RETRIES:
                    raise
                wait = 2 ** attempt
                logger.warning(f"报告生成流式调用失败 (第{attempt}次)，{wait}s 后重试: {e}")
                time.sleep(wait)
                full_content.clear()

        content = "".join(full_content)
        title = query
        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("# ") and not line.startswith("## "):
                title = line.lstrip("# ").strip()
                break

        yield DeepReport(
            title=title,
            content=content,
            citations=citations,
            query=query,
            source_count=len(retrieved_results),
            word_count=len(content),
            retrieval_rounds=retrieval_rounds,
            web_search_used=web_search_used,
        )

    def _build_context(self, results: List[Dict[str, Any]]):
        """构建带编号的上下文和引用列表。"""
        context_parts = []
        citations = []

        for idx, result in enumerate(results, 1):
            meta = result.get("metadata", {})
            source_type = meta.get("source_type", "unknown")
            source_type_label = {
                "file": "文件", "web": "网页", "web_search": "联网搜索",
            }.get(source_type, source_type)

            context_parts.append(CONTEXT_TEMPLATE.format(
                idx=idx,
                source_type=source_type_label,
                source_name=meta.get("source_name", "未知"),
                text=result.get("text", ""),
            ))

            snippet = result.get("original_text") or result.get("text", "")
            if len(snippet) > 200:
                snippet = snippet[:200] + "..."

            citations.append(ReportCitation(
                index=idx,
                source_type=source_type,
                source_name=meta.get("source_name", "未知"),
                title=meta.get("title", ""),
                source_url=meta.get("source_url"),
                text_snippet=snippet,
            ))

        return "\n".join(context_parts), citations

    def _chat_with_retry(self, messages: list, max_tokens: int = 3000) -> str:
        """调用 Chat API，失败时自动重试。"""
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                return self.chat_client.complete(
                    messages=messages,
                    temperature=0.3,
                    max_tokens=max_tokens,
                )
            except RETRYABLE_ERRORS as e:
                if attempt == MAX_RETRIES:
                    raise
                wait = 2 ** attempt
                logger.warning(f"报告生成 API 调用失败 (第{attempt}次)，{wait}s 后重试: {e}")
                time.sleep(wait)
