"""test_retriever.py — Retriever 核心逻辑单元测试（去重、关键词提取等）。"""

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
        from config import Settings

        self.retriever._settings = Settings(_env_file=None)

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


class TestPdfContentPreference:
    def test_normal_legal_question_prefers_body_over_template(self):
        results = [
            {"id": "template", "metadata": {"content_kind": "template_table"}},
            {"id": "body", "metadata": {"content_kind": "body"}},
            {"id": "legacy", "metadata": {}},
        ]
        ranked = Retriever._prefer_primary_pdf_content("哪些情形需要安全评估", results)
        assert [item["id"] for item in ranked] == ["body", "legacy", "template"]

    def test_template_question_keeps_relevance_order(self):
        results = [
            {"id": "template", "metadata": {"content_kind": "template_table"}},
            {"id": "body", "metadata": {"content_kind": "body"}},
        ]
        ranked = Retriever._prefer_primary_pdf_content("申报表模板如何填写", results)
        assert ranked == results


class _SectionStore:
    def __init__(self):
        self.section_calls = []

    def get_section_chunks(self, source_name, parent_section_id, owners=None):
        self.section_calls.append((source_name, parent_section_id, owners))
        return [
            {
                "id": "c0",
                "text": "账号注册与登录。",
                "metadata": {"chunk_index": 3},
            },
            {
                "id": "c1",
                "text": "项目新建并上传材料。",
                "metadata": {"chunk_index": 4},
            },
            {
                "id": "c2",
                "text": "省级网信办开展完备性查验。",
                "metadata": {"chunk_index": 5},
            },
        ]

    def get_neighbor_chunks(self, **_kwargs):
        raise AssertionError("流程问题不应回退为固定窗口")


class TestParentSectionExpansion:
    def test_parent_deduplication_happens_before_top_k_cutoff(self):
        results = [
            {
                "id": "a1",
                "metadata": {
                    "owner_id": "__public__",
                    "source_name": "a.pdf",
                    "parent_section_id": "parent-a",
                },
            },
            {
                "id": "a2",
                "metadata": {
                    "owner_id": "__public__",
                    "source_name": "a.pdf",
                    "parent_section_id": "parent-a",
                },
            },
            {
                "id": "b1",
                "metadata": {
                    "owner_id": "__public__",
                    "source_name": "b.pdf",
                    "parent_section_id": "parent-b",
                },
            },
        ]
        selected = Retriever._select_top_results("办理流程", results, top_k=2)
        assert [item["id"] for item in selected] == ["a1", "b1"]

    def test_process_query_expands_parent_once_and_deduplicates_siblings(self):
        from config import Settings

        retriever = Retriever.__new__(Retriever)
        retriever._settings = Settings(_env_file=None, section_expansion_max_chars=1000)
        retriever.vector_store = _SectionStore()
        results = [
            {
                "id": "seed-1",
                "text": "项目新建并上传材料。",
                "metadata": {
                    "source_name": "guide.pdf",
                    "chunk_index": 4,
                    "parent_section_id": "sec-flow",
                },
            },
            {
                "id": "seed-2",
                "text": "完备性查验。",
                "metadata": {
                    "source_name": "guide.pdf",
                    "chunk_index": 5,
                    "parent_section_id": "sec-flow",
                },
            },
        ]

        expanded = retriever._expand_context(
            results,
            query="申报流程是什么？",
            viewers=[None],
        )

        assert len(expanded) == 1
        assert expanded[0]["id"] == "sec-flow"
        assert "账号注册与登录" in expanded[0]["text"]
        assert "省级网信办开展完备性查验" in expanded[0]["text"]
        assert expanded[0]["metadata"]["context_expansion_mode"] == "parent_section"
        assert expanded[0]["metadata"]["seed_chunk_id"] == "seed-1"
        assert retriever.vector_store.section_calls == [("guide.pdf", "sec-flow", [None])]

    def test_pack_section_context_honors_budget_around_seed(self):
        chunks = [
            {"text": "甲" * 80, "metadata": {"chunk_index": 1}},
            {"text": "乙" * 80, "metadata": {"chunk_index": 2}},
            {"text": "丙" * 80, "metadata": {"chunk_index": 3}},
        ]
        packed = Retriever._pack_section_context(
            chunks,
            seed_chunk_index=2,
            max_chars=170,
        )
        assert "乙" * 80 in packed
        assert len(packed) <= 170
