"""文本清洗模块：对原始文本做标准化处理。"""

import re
import logging

logger = logging.getLogger(__name__)

# 需要统一为普通空格的 Unicode 空白字符
_UNICODE_SPACES = str.maketrans({
    "\u3000": " ",   # 全角空格（ideographic space，常见于中文格式文档）
    "\u00a0": " ",   # 不换行空格（non-breaking space，常见于网页复制）
    "\u200b": "",    # 零宽空格（zero-width space，常见于复制粘贴）
    "\u200c": "",    # 零宽不连字
    "\u200d": "",    # 零宽连字
    "\ufeff": "",    # BOM（Byte Order Mark）
})


class TextCleaner:
    """对原始文本执行清洗操作。"""

    def clean(self, text: str) -> str:
        """
        清洗文本内容：
        1. 中文特有字符清洗（全角空格、零宽字符、BOM 等）
        2. 统一换行符为 \\n
        3. 去除 NULL 字节等控制字符
        4. 合并行内多余空格为单个空格
        5. 去除每行首尾空白
        6. 合并连续空行为最多两个换行
        7. 去除整体首尾空白
        """
        if not text:
            return ""

        # 1. 中文特有：统一 Unicode 空白字符，去除零宽字符
        text = text.translate(_UNICODE_SPACES)

        # 2. 统一换行符
        text = text.replace("\r\n", "\n").replace("\r", "\n")

        # 3. 去除 NULL 字节及其他控制字符（保留换行和制表符）
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)

        # 4. 合并行内连续空格为单个空格（不影响换行）
        text = re.sub(r"[ \t]+", " ", text)

        # 5. 去除每行首尾空白
        lines = [line.strip() for line in text.split("\n")]
        text = "\n".join(lines)

        # 6. 合并连续空行（3 个及以上换行 → 2 个换行）
        text = re.sub(r"\n{3,}", "\n\n", text)

        # 7. 去除首尾空白
        text = text.strip()

        logger.debug(f"清洗完成，文本长度: {len(text)}")
        return text
