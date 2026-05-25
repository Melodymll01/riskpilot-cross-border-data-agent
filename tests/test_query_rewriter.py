"""test_query_rewriter.py — QueryRewriter 规则层测试（不调用 LLM）。"""

import pytest
from retrieval.search.query_rewriter import QueryRewriter, DOMAIN_SYNONYMS


class TestDomainSynonyms:
    """领域同义词规则化测试。"""

    def setup_method(self):
        self.rewriter = QueryRewriter()

    def test_normalize_slang(self):
        """口语/缩写应被替换为标准术语。"""
        result = self.rewriter._normalize("我们公司要出海传数据怎么办")
        assert "数据出境" in result

    def test_normalize_abbreviation(self):
        """法规缩写应被展开。"""
        assert "个人信息保护法" in self.rewriter._normalize("PIPL的规定")
        assert "个人信息保护法" in self.rewriter._normalize("个保法要求")
        assert "网络安全法" in self.rewriter._normalize("网安法第21条")

    def test_normalize_org_abbreviation(self):
        """机构缩写应被替换。"""
        assert "国家互联网信息办公室" in self.rewriter._normalize("网信办发布的规定")
        assert "国家互联网信息办公室" in self.rewriter._normalize("CAC的通知")

    def test_normalize_no_change(self):
        """不包含缩写的文本应保持不变。"""
        text = "数据出境安全评估的具体流程是什么"
        assert self.rewriter._normalize(text) == text


class TestValidation:
    """改写结果后处理校验。"""

    def setup_method(self):
        self.rewriter = QueryRewriter()

    def test_filter_too_short(self):
        """过短的改写应被过滤。"""
        result = self.rewriter._validate("原始查询", ["ab", "cd", "正常的改写查询"])
        assert "ab" not in result
        assert "正常的改写查询" in result

    def test_filter_too_long(self):
        """过长的改写应被过滤。"""
        long_query = "a" * 201
        result = self.rewriter._validate("原始查询", [long_query, "正常查询"])
        assert long_query not in result

    def test_filter_duplicate_of_original(self):
        """与原始查询完全相同的改写应被过滤。"""
        result = self.rewriter._validate("数据出境", ["数据出境", "数据出境安全评估"])
        assert result == ["数据出境安全评估"]

    def test_strip_numbering(self):
        """LLM 输出的编号前缀应被去除。"""
        result = self.rewriter._validate("原始", ["1. 改写查询一", "2. 改写查询二"])
        assert "改写查询一" in result
        assert "改写查询二" in result

    def test_fallback_to_original(self):
        """所有改写都无效时应回退到原始查询。"""
        result = self.rewriter._validate("原始查询", ["ab", ""])
        assert result == ["原始查询"]


class TestCache:
    """LRU 缓存测试。"""

    def setup_method(self):
        self.rewriter = QueryRewriter()

    def test_cache_put_and_get(self):
        self.rewriter._put_cache("key1", ["val1"])
        assert "key1" in self.rewriter._cache
        assert self.rewriter._cache["key1"] == ["val1"]

    def test_cache_eviction(self):
        """超过容量时应淘汰最旧条目。"""
        # 手动缩小缓存容量测试淘汰
        import retrieval.search.query_rewriter as qr
        original_size = qr._CACHE_MAX_SIZE
        qr._CACHE_MAX_SIZE = 3
        try:
            self.rewriter._cache.clear()
            for i in range(5):
                self.rewriter._put_cache(f"key{i}", [f"val{i}"])
            # 前两个应已被淘汰
            assert "key0" not in self.rewriter._cache
            assert "key1" not in self.rewriter._cache
            assert "key4" in self.rewriter._cache
        finally:
            qr._CACHE_MAX_SIZE = original_size
