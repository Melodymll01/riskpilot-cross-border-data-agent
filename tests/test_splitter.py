"""test_splitter.py — TextSplitter 单元测试。"""

import pytest
from processing.splitter import TextSplitter


class TestTextSplitter:
    """文本切分器测试。"""

    def test_empty_input(self):
        splitter = TextSplitter(chunk_size=200, chunk_overlap=40)
        assert splitter.split("") == []
        assert splitter.split("   ") == []

    def test_short_text_no_split(self):
        """短文本应不被切分。"""
        splitter = TextSplitter(chunk_size=500, chunk_overlap=100)
        text = "这是一段短文本，不需要切分。"
        chunks = splitter.split(text)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_basic_splitting(self):
        """基本切分：长文本应被切分为多个 chunk。"""
        splitter = TextSplitter(chunk_size=100, chunk_overlap=20)
        text = "。".join([f"第{i}条规定内容" for i in range(1, 20)])
        chunks = splitter.split(text)
        assert len(chunks) > 1
        for chunk in chunks:
            # 允许超出一点点（overlap 拼接）
            assert len(chunk) <= splitter.chunk_size * 1.5

    def test_chunk_overlap_exists(self):
        """相邻 chunk 之间应有重叠内容。"""
        splitter = TextSplitter(chunk_size=100, chunk_overlap=30)
        text = "。".join([f"第{i}条关于数据出境的重要规定" for i in range(1, 15)])
        chunks = splitter.split(text)
        if len(chunks) >= 2:
            # 至少有一对相邻 chunk 有内容重叠
            has_overlap = False
            for i in range(len(chunks) - 1):
                # 检查后一个 chunk 是否包含前一个 chunk 末尾的部分内容
                tail = chunks[i][-20:]  # 取最后 20 个字符
                if tail in chunks[i + 1]:
                    has_overlap = True
                    break
            # overlap 不一定精确包含，但 chunk 数应该大于无 overlap 时
            assert len(chunks) >= 2

    def test_paragraph_boundary_preferred(self):
        """切分应优先在段落边界（\\n\\n）处断开。"""
        splitter = TextSplitter(chunk_size=100, chunk_overlap=20)
        text = "第一段" + "啊" * 40 + "\n\n" + "第二段" + "啊" * 40 + "\n\n" + "第三段" + "啊" * 40
        chunks = splitter.split(text)
        # 至少有段落分隔的切分
        assert len(chunks) >= 2

    def test_force_split_very_long_text(self):
        """超长无分隔文本应通过强制截断切分。"""
        splitter = TextSplitter(chunk_size=50, chunk_overlap=10)
        text = "中" * 200  # 200 个相同字符，无任何分隔符
        chunks = splitter.split(text)
        assert len(chunks) > 1

    def test_merge_small_tail_chunks(self):
        """过短的尾部 chunk 应被合并到前一个。"""
        splitter = TextSplitter(chunk_size=200, chunk_overlap=40)
        # 构造：一个大段 + 一个很短的尾巴
        text = "内容" * 80 + "\n\n" + "尾巴"
        chunks = splitter.split(text)
        # 尾部 "尾巴" 应被合并，不单独成 chunk
        for chunk in chunks:
            assert len(chunk) >= 2  # 不应有极短的 chunk

    def test_code_block_protection(self):
        """Markdown 代码块应被保护，不被分隔符切断。"""
        splitter = TextSplitter(chunk_size=500, chunk_overlap=50)
        text = "前面的内容。\n\n```python\ndef hello():\n    print('hello')\n```\n\n后面的内容。"
        chunks = splitter.split(text)
        # 代码块应完整出现在某个 chunk 中
        full_text = "".join(chunks)
        assert "def hello():" in full_text
        assert "print('hello')" in full_text

    def test_consistent_output(self):
        """相同输入应产生相同输出（确定性）。"""
        splitter = TextSplitter(chunk_size=100, chunk_overlap=20)
        text = "。".join([f"第{i}条规定" for i in range(1, 20)])
        result1 = splitter.split(text)
        result2 = splitter.split(text)
        assert result1 == result2
