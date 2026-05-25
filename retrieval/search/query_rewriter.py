"""查询改写模块：利用 LLM 将用户口语化提问改写为更适合检索的查询。

核心价值：
- 用户的提问用词往往与文档原文不一致，导致向量相似度偏低
- 改写后的查询更接近文档表述，显著提升召回率

优化点：
- 规则预处理：领域同义词/缩写标准化（确定性）
- LRU 缓存：相同查询不重复调用 LLM（进程内存级）
- 后处理校验：过滤过短/过长/重复的改写
- 保留原始查询：与改写结果一起做多路召回
"""

import logging
import re
from collections import OrderedDict

from config import settings
from retrieval.generation.chat_client import ChatClient, RETRYABLE_ERRORS

logger = logging.getLogger(__name__)

# ── 领域同义词映射（规则预处理层） ──
DOMAIN_SYNONYMS = {
    "出海": "数据出境",
    "传数据到国外": "跨境数据传输",
    "安评": "数据出境安全评估",
    "个保法": "个人信息保护法",
    "PIPL": "个人信息保护法",
    "SCC": "个人信息出境标准合同",
    "标准合同": "个人信息出境标准合同",
    "网安法": "网络安全法",
    "数安法": "数据安全法",
    "CAC": "国家互联网信息办公室",
    "网信办": "国家互联网信息办公室",
    "重要数据": "重要数据识别与分类",
    "合规审计": "个人信息保护合规审计",
}

REWRITE_PROMPT = """你是一个查询优化助手。请将用户的口语化问题改写为更适合在数据出境法规知识库中检索的精确查询。

要求：
1. 提取核心关键词和专业术语
2. 去除口语化表达、语气词
3. 如果问题涉及多个方面，拆分为最多 3 个独立的检索查询
4. 每个查询独占一行，不要编号
5. 直接输出改写后的查询，不要解释

示例：
用户问题：公司要往国外传数据需要干啥？
改写查询：
数据出境合规要求
跨境数据传输审批流程
数据出境安全评估申报条件

用户问题：{query}
"""

# ── 缓存容量 ──
_CACHE_MAX_SIZE = 256


class QueryRewriter:
    """查询改写器：规则预处理 → LLM 改写 → 后处理校验，三段式管线。"""

    def __init__(self):
        self.chat_client = ChatClient()
        self.enabled = settings.enable_query_rewrite
        # LRU 缓存：OrderedDict 实现，存在进程内存中，进程重启即清空
        self._cache: OrderedDict[str, list[str]] = OrderedDict()

    def rewrite(self, query: str) -> list[str]:
        """
        改写用户查询。

        流程：规则预处理 → 缓存检查 → LLM 改写 → 后处理校验 → 保留原始查询

        Args:
            query: 原始用户提问

        Returns:
            改写后的查询列表（含原始查询），第一条为原始查询
        """
        if not self.enabled:
            return [query]

        # ① 规则预处理：同义词/缩写标准化
        normalized = self._normalize(query)

        # ② 缓存命中检查
        if normalized in self._cache:
            self._cache.move_to_end(normalized)  # 刷新 LRU 顺序
            cached = self._cache[normalized]
            logger.debug(f"查询改写缓存命中: '{normalized[:50]}'")
            return cached

        # ③ LLM 改写
        try:
            rewritten = self.chat_client.complete(
                messages=[
                    {"role": "user", "content": REWRITE_PROMPT.format(query=normalized)},
                ],
                temperature=0.0,
                max_tokens=1000,
            )

            # ④ 后处理：清洗 + 校验
            raw_queries = [q.strip() for q in rewritten.split("\n") if q.strip()]
            validated = self._validate(normalized, raw_queries)

            # ⑤ 保留原始查询在首位，与改写结果一起多路召回
            result = [query] + [q for q in validated if q != query]
            result = result[:4]  # 原始 + 最多 3 条改写

            # 写入缓存
            self._put_cache(normalized, result)

            logger.info(f"查询改写: '{query[:50]}' → {result}")
            return result

        except Exception as e:
            logger.warning(f"查询改写失败，使用原始查询: {e}")
            return [query]

    def _normalize(self, query: str) -> str:
        """规则预处理：将领域缩写/黑话替换为标准术语。"""
        result = query
        for slang, standard in DOMAIN_SYNONYMS.items():
            result = result.replace(slang, standard)
        return result

    def _validate(self, original: str, queries: list[str]) -> list[str]:
        """后处理校验：过滤低质量改写。"""
        valid = []
        for q in queries:
            # 去除 LLM 可能输出的编号前缀（如 "1. "、"- "）
            q = re.sub(r"^[\d]+[.、)\-]\s*", "", q).strip()
            if not q:
                continue
            if len(q) < 4 or len(q) > 200:
                continue
            if q == original:
                continue
            valid.append(q)
        return valid if valid else [original]

    def _put_cache(self, key: str, value: list[str]) -> None:
        """写入缓存，超过容量时淘汰最旧条目。"""
        if key in self._cache:
            self._cache.move_to_end(key)
        else:
            if len(self._cache) >= _CACHE_MAX_SIZE:
                self._cache.popitem(last=False)  # 淘汰最久未使用的
        self._cache[key] = value
