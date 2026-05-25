"""PDF 抽取器：在 PyPDF2 基础上叠加页眉页脚剔除、表格结构化抽取。

设计原则：
- 单一职责：只负责 PDF -> 文本，不关心上层业务
- 可配置：通过构造参数开关各项增强能力，便于按数据源差异化使用
- 渐进降级：表格抽取依赖 pdfplumber，未安装时回退到纯文本模式
"""

import logging
import re
from collections import Counter
from pathlib import Path
from typing import List, Optional

from PyPDF2 import PdfReader

logger = logging.getLogger(__name__)

try:
    import pdfplumber  # type: ignore
    _HAS_PDFPLUMBER = True
except ImportError:  # pragma: no cover
    _HAS_PDFPLUMBER = False


class PDFExtractor:
    """PDF 文本抽取器，支持页眉页脚剔除与表格结构化抽取。

    Args:
        remove_headers_footers: 是否启用跨页重复行检测剔除页眉页脚
        extract_tables: 是否将表格抽取为 Markdown 表格（依赖 pdfplumber）
        head_n: 每页前 N 行参与页眉检测
        tail_n: 每页后 N 行参与页脚检测
        repeat_ratio: 跨页重复比例阈值，>= 该比例的页面出现该行即判为页眉/页脚
        min_pages_for_detection: 页数低于此值不做页眉页脚检测
    """

    def __init__(
        self,
        remove_headers_footers: bool = True,
        extract_tables: bool = True,
        head_n: int = 2,
        tail_n: int = 2,
        repeat_ratio: float = 0.5,
        min_pages_for_detection: int = 3,
    ):
        self.remove_headers_footers = remove_headers_footers
        self.extract_tables = extract_tables and _HAS_PDFPLUMBER
        self.head_n = head_n
        self.tail_n = tail_n
        self.repeat_ratio = repeat_ratio
        self.min_pages_for_detection = min_pages_for_detection

        if extract_tables and not _HAS_PDFPLUMBER:
            logger.warning("未安装 pdfplumber，表格抽取功能已自动降级为纯文本模式")

    # ---------- 公共入口 ----------

    def extract(self, path: Path) -> str:
        """抽取 PDF 全部文本。

        Returns:
            清洗后的文本，页之间用两个换行分隔
        """
        if self.extract_tables:
            pages = self._extract_with_tables(path)
        else:
            pages = self._extract_text_only(path)

        if not any(p.strip() for p in pages):
            raise ValueError("PDF 文件未能提取到任何文本（可能为扫描件或纯图片 PDF）")

        # 页眉页脚剔除：基于行级跨页重复
        if self.remove_headers_footers:
            pages = self._strip_headers_footers(pages)

        return "\n\n".join(p for p in pages if p.strip())

    # ---------- 文本抽取（PyPDF2，兜底通道）----------

    def _extract_text_only(self, path: Path) -> List[str]:
        """纯文本抽取：使用 PyPDF2，按页返回。"""
        reader = PdfReader(str(path))
        pages: List[str] = []
        for i, page in enumerate(reader.pages):
            try:
                text = page.extract_text() or ""
            except Exception as e:
                logger.warning(f"PDF 第 {i + 1} 页提取失败，已跳过: {e}")
                text = ""
            pages.append(text)
        return pages

    # ---------- 文本 + 表格抽取（pdfplumber 通道）----------

    def _extract_with_tables(self, path: Path) -> List[str]:
        """文本 + 表格抽取：表格区域单独序列化为 Markdown，正文排除该区域。"""
        pages: List[str] = []
        with pdfplumber.open(str(path)) as pdf:
            for i, page in enumerate(pdf.pages):
                try:
                    page_text = self._extract_single_page_with_tables(page)
                except Exception as e:
                    logger.warning(f"PDF 第 {i + 1} 页抽取失败，回退纯文本: {e}")
                    try:
                        page_text = page.extract_text() or ""
                    except Exception:
                        page_text = ""
                pages.append(page_text)
        return pages

    def _extract_single_page_with_tables(self, page) -> str:
        """单页：抽取表格 bbox，正文 filter 排除表格区域，最后拼接。"""
        try:
            tables = page.find_tables() or []
        except Exception:
            tables = []

        table_bboxes = [t.bbox for t in tables]

        # 1. 抽取正文（排除表格区域）
        if table_bboxes:
            def _outside_tables(obj):
                # 判断 word/char 中心点是否落在任一表格 bbox 内
                cx = (obj.get("x0", 0) + obj.get("x1", 0)) / 2
                cy = (obj.get("top", 0) + obj.get("bottom", 0)) / 2
                for x0, top, x1, bottom in table_bboxes:
                    if x0 <= cx <= x1 and top <= cy <= bottom:
                        return False
                return True

            try:
                filtered_page = page.filter(_outside_tables)
                non_table_text = filtered_page.extract_text() or ""
            except Exception:
                # filter 在某些 PDF 上会失败，退回不过滤
                non_table_text = page.extract_text() or ""
        else:
            non_table_text = page.extract_text() or ""

        # 2. 序列化每个表格为 Markdown
        md_tables: List[str] = []
        for table in tables:
            try:
                rows = table.extract()
            except Exception:
                continue
            if not rows or len(rows) < 2:
                continue
            md = self._rows_to_markdown(rows)
            if md:
                md_tables.append(md)

        if md_tables:
            # 表格放在正文之后，splitter 的 markdown 表格保护逻辑会保持其完整性
            return non_table_text + "\n\n" + "\n\n".join(md_tables)
        return non_table_text

    @staticmethod
    def _rows_to_markdown(rows: List[List[Optional[str]]]) -> str:
        """二维行列表 -> Markdown 表格字符串。"""
        # 归一化单元格：None -> ""，去换行
        norm: List[List[str]] = []
        for row in rows:
            norm.append([(c or "").strip().replace("\n", " ") for c in row])

        # 过滤完全空行
        norm = [r for r in norm if any(c for c in r)]
        if len(norm) < 2:
            return ""

        col_count = max(len(r) for r in norm)
        # 长度对齐
        norm = [r + [""] * (col_count - len(r)) for r in norm]

        header = norm[0]
        body = norm[1:]

        lines = ["| " + " | ".join(header) + " |"]
        lines.append("| " + " | ".join(["---"] * col_count) + " |")
        for r in body:
            lines.append("| " + " | ".join(r) + " |")
        return "\n".join(lines)

    # ---------- 页眉页脚剔除 ----------

    def _strip_headers_footers(self, pages: List[str]) -> List[str]:
        """跨页重复行检测，剔除页眉页脚。"""
        if len(pages) < self.min_pages_for_detection:
            return pages

        # 每页拆行
        pages_lines: List[List[str]] = [
            [ln.strip() for ln in p.splitlines() if ln.strip()] for p in pages
        ]

        # 收集每页头尾候选行的归一化形式
        counter: Counter = Counter()
        for lines in pages_lines:
            if not lines:
                continue
            candidates = lines[: self.head_n] + lines[-self.tail_n:]
            # 同页去重，避免一页内重复行被双倍计数
            seen_in_page = set()
            for ln in candidates:
                norm = self._normalize_for_match(ln)
                if norm and norm not in seen_in_page:
                    counter[norm] += 1
                    seen_in_page.add(norm)
        # 计算重复行阈值：出现次数 >= threshold 的行视为页眉/页脚模式
        threshold = max(2, int(len(pages_lines) * self.repeat_ratio))
        repeated = {ln for ln, c in counter.items() if c >= threshold}

        if not repeated:
            return pages

        logger.info(f"检测到 {len(repeated)} 个页眉/页脚模式，将从 {len(pages)} 页中剔除")

        # 仅在头尾位置剔除，避免误删正文中偶然重复的句子
        cleaned_pages: List[str] = []
        for lines in pages_lines:
            if not lines:
                cleaned_pages.append("")
                continue
            head_keep_start = 0
            for i in range(min(self.head_n, len(lines))):
                if self._normalize_for_match(lines[i]) in repeated:
                    head_keep_start = i + 1
                else:
                    break
            tail_keep_end = len(lines)
            for i in range(1, min(self.tail_n, len(lines)) + 1):
                if self._normalize_for_match(lines[-i]) in repeated:
                    tail_keep_end = len(lines) - i
                else:
                    break
            kept = lines[head_keep_start:tail_keep_end]
            cleaned_pages.append("\n".join(kept))
        return cleaned_pages

    @staticmethod
    def _normalize_for_match(line: str) -> str:
        """归一化用于跨页匹配：把数字替换为占位符，使带页码的行也能聚类。"""
        # 多个连续数字视为同一占位符
        return re.sub(r"\d+", "#", line).strip()
