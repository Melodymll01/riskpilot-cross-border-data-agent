"""test_cleaner.py — TextCleaner 单元测试。"""

from processing.cleaner import TextCleaner


class TestTextCleaner:
    """文本清洗器测试。"""

    def setup_method(self):
        self.cleaner = TextCleaner()

    def test_empty_input(self):
        assert self.cleaner.clean("") == ""
        assert self.cleaner.clean("   ") == ""

    def test_unicode_spaces(self):
        """全角空格、不换行空格应替换为普通空格。"""
        text = "数据\u3000出境\u00a0评估"
        result = self.cleaner.clean(text)
        assert "\u3000" not in result
        assert "\u00a0" not in result
        assert "数据 出境 评估" == result

    def test_zero_width_chars(self):
        """零宽字符应被移除。"""
        text = "数据\u200b出境\u200c评估\ufeff"
        result = self.cleaner.clean(text)
        assert "\u200b" not in result
        assert "\u200c" not in result
        assert "\ufeff" not in result
        assert result == "数据出境评估"

    def test_normalize_newlines(self):
        """\\r\\n 和 \\r 应统一为 \\n。"""
        text = "第一行\r\n第二行\r第三行"
        result = self.cleaner.clean(text)
        assert "\r" not in result
        assert result == "第一行\n第二行\n第三行"

    def test_null_bytes_removed(self):
        """NULL 字节和控制字符应被移除。"""
        text = "数据\x00出境\x07评估"
        result = self.cleaner.clean(text)
        assert "\x00" not in result
        assert "\x07" not in result

    def test_collapse_spaces(self):
        """连续空格应合并为单个。"""
        text = "数据   出境    评估"
        result = self.cleaner.clean(text)
        assert result == "数据 出境 评估"

    def test_collapse_blank_lines(self):
        """连续 3 行以上空行应合并为 2 行。"""
        text = "第一段\n\n\n\n\n第二段"
        result = self.cleaner.clean(text)
        assert result == "第一段\n\n第二段"

    def test_strip_line_whitespace(self):
        """每行首尾空白应被去除。"""
        text = "  第一行  \n  第二行  "
        result = self.cleaner.clean(text)
        assert result == "第一行\n第二行"

    def test_comprehensive(self):
        """综合测试：多种问题同时出现。"""
        text = "  \u3000数据\u200b出境\r\n\r\n\r\n  \u00a0  评估\x00  "
        result = self.cleaner.clean(text)
        assert "\u3000" not in result
        assert "\u200b" not in result
        assert "\r" not in result
        assert "\x00" not in result
        assert result  # 非空
