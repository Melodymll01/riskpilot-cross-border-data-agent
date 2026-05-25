"""test_metadata.py — Metadata 构建和处理链路测试。"""

import pytest
from processing.metadata import build_chunks, ChunkWithMetadata
from ingestion.unified_loader import RawDocument


class TestChunkWithMetadata:
    """ChunkWithMetadata 数据结构测试。"""

    def test_to_metadata_dict(self):
        chunk = ChunkWithMetadata(
            chunk_id="test-id-001",
            text="测试文本内容",
            source_type="file",
            source_name="test.pdf",
            title="测试文档",
            source_url=None,
            chunk_index=0,
            category="法规",
        )
        meta = chunk.to_metadata_dict()
        assert meta["chunk_id"] == "test-id-001"
        assert meta["source_type"] == "file"
        assert meta["source_name"] == "test.pdf"
        assert meta["title"] == "测试文档"
        assert meta["chunk_index"] == 0
        assert meta["category"] == "法规"
        assert "imported_at" in meta
        assert "source_url" not in meta  # None 不应出现在 metadata

    def test_to_metadata_dict_with_url(self):
        chunk = ChunkWithMetadata(
            chunk_id="test-id-002",
            text="测试",
            source_type="web",
            source_name="example.com",
            title="示例网页",
            source_url="https://example.com/article",
            chunk_index=1,
        )
        meta = chunk.to_metadata_dict()
        assert meta["source_url"] == "https://example.com/article"


class TestBuildChunks:
    """文档处理链路 build_chunks 集成测试。"""

    def test_basic_build(self, raw_document):
        """基本的切分 + 元数据构建。"""
        chunks = build_chunks(raw_document)
        assert len(chunks) >= 1
        for chunk in chunks:
            assert chunk.source_type == "file"
            assert chunk.source_name == "test_doc.pdf"
            assert chunk.title == "测试文档"
            assert chunk.chunk_id  # UUID 不为空
            assert chunk.text.strip()  # 文本不为空

    def test_chunk_index_sequential(self, raw_document):
        """chunk_index 应从 0 开始递增。"""
        chunks = build_chunks(raw_document)
        for i, chunk in enumerate(chunks):
            assert chunk.chunk_index == i

    def test_chunk_ids_unique(self, raw_document):
        """每个 chunk 的 ID 应唯一。"""
        chunks = build_chunks(raw_document)
        ids = [c.chunk_id for c in chunks]
        assert len(ids) == len(set(ids))

    def test_empty_document(self):
        """空文档应返回空列表。"""
        doc = RawDocument(
            content="",
            source_type="file",
            source_name="empty.txt",
            title="空文档",
        )
        chunks = build_chunks(doc)
        assert chunks == []

    def test_web_document(self):
        """网页文档也应正常处理。"""
        doc = RawDocument(
            content="这是网页内容。" * 50,
            source_type="web",
            source_name="示例网站",
            title="网页标题",
            source_url="https://example.com",
        )
        chunks = build_chunks(doc)
        assert len(chunks) >= 1
        assert chunks[0].source_type == "web"
        assert chunks[0].source_url == "https://example.com"

    def test_cleaning_applied(self):
        """清洗步骤应被执行（零宽字符、全角空格等）。"""
        doc = RawDocument(
            content="数据\u200b出境\u3000评估\x00规定",
            source_type="file",
            source_name="dirty.txt",
            title="脏数据",
        )
        chunks = build_chunks(doc)
        assert len(chunks) >= 1
        text = chunks[0].text
        assert "\u200b" not in text
        assert "\u3000" not in text
        assert "\x00" not in text
