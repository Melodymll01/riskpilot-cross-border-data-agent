"""test_retriever.py — Retriever 核心逻辑单元测试（去重、关键词提取等）。"""

import pytest
from retrieval.search.retriever import Retriever


class TestKeywordExtraction:
    """关键词提取测试——纯规则逻辑，不依赖外部服务。"""

    def test_law_reference(self):
        """法条编号提取：第X条、第X章。"""
        keywords = Retriever._extract_keywords("根据第四条规定申报安全评估")
        assert "第四条" in keywords

    def test_law_reference_chinese_numerals(self):
        """中文数字法条编号。"""
        keywords = Retriever._extract_keywords("第三十八条个人信息出境")
        assert "第三十八条" in keywords

    def test_quoted_terms(self):
        """引号内术语提取。"""
        keywords = Retriever._extract_keywords('关于"数据出境安全评估"的规定')
        assert "数据出境安全评估" in keywords

    def test_domain_terms(self):
        """领域关键词匹配。"""
        keywords = Retriever._extract_keywords("关于个人信息保护和数据安全的法规")
        assert "个人信息保护" in keywords
        assert "数据安全" in keywords

    def test_no_duplicates(self):
        """关键词不应重复。"""
        keywords = Retriever._extract_keywords('"数据安全"与数据安全法的关系')
        assert len(keywords) == len(set(keywords))

    def test_empty_query(self):
        """空查询应返回空列表。"""
        keywords = Retriever._extract_keywords("")
        assert keywords == []


class TestTextDeduplication:
    """文本去重测试。"""

    def setup_method(self):
        self.retriever = Retriever.__new__(Retriever)

    def test_identical_texts(self):
        """完全相同的文本应被去重。"""
        results = [
            {"id": "1", "text": "这是一段重复的文本内容"},
            {"id": "2", "text": "这是一段重复的文本内容"},
        ]
        deduped = self.retriever._deduplicate(results)
        assert len(deduped) == 1

    def test_highly_overlapping(self):
        """高度重叠的文本应被去重（模拟 overlap 造成的重复）。"""
        base = "数据出境安全评估的触发条件包括以下几种情形"
        results = [
            {"id": "1", "text": base + "第一种情形"},
            {"id": "2", "text": base + "第二种情形"},  # 大部分相同
        ]
        deduped = self.retriever._deduplicate(results)
        # 高度重叠应被去重
        assert len(deduped) <= 2

    def test_different_texts_kept(self):
        """不同的文本应都保留。"""
        results = [
            {"id": "1", "text": "数据出境安全评估是一项重要的制度安排"},
            {"id": "2", "text": "个人信息保护法对跨境数据传输有明确要求"},
        ]
        deduped = self.retriever._deduplicate(results)
        assert len(deduped) == 2

    def test_single_result(self):
        """单条结果不需要去重。"""
        results = [{"id": "1", "text": "只有一条"}]
        deduped = self.retriever._deduplicate(results)
        assert len(deduped) == 1

    def test_empty_results(self):
        """空结果集应返回空。"""
        deduped = self.retriever._deduplicate([])
        assert deduped == []


class TestTextOverlapRatio:
    """文本重叠率计算测试。"""

    def test_identical(self):
        ratio = Retriever._text_overlap_ratio("完全相同的文本", "完全相同的文本")
        assert ratio == 1.0

    def test_substring(self):
        ratio = Retriever._text_overlap_ratio("子串", "这是一个子串的例子")
        assert ratio == 1.0

    def test_no_overlap(self):
        ratio = Retriever._text_overlap_ratio("ABCDEF", "数据出境评估")
        assert ratio < 0.3

    def test_empty_text(self):
        assert Retriever._text_overlap_ratio("", "某段文本") == 0.0
        assert Retriever._text_overlap_ratio("某段文本", "") == 0.0
        assert Retriever._text_overlap_ratio("", "") == 0.0
