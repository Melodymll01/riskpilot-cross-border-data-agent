"""PDF 抽取器：在 PyMuPDF 文本层抽取基础上叠加页眉页脚剔除、表格结构化抽取、扫描页 OCR 降级。

设计原则：
- 单一职责：只负责 PDF -> 文本，不关心上层业务
- 可配置：通过构造参数开关各项增强能力，便于按数据源差异化使用
- 渐进降级：表格抽取依赖 pdfplumber；文本层缺失页回退到 RapidOCR 本地识别，依赖未装时自动关闭
"""

import logging
import re
from collections import Counter
from pathlib import Path
from typing import List, Optional

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

try:
    import pdfplumber  # type: ignore
    _HAS_PDFPLUMBER = True
except ImportError:  # pragma: no cover
    _HAS_PDFPLUMBER = False

try:
    from rapidocr_onnxruntime import RapidOCR  # type: ignore
    _HAS_OCR = True
except ImportError:  # pragma: no cover
    _HAS_OCR = False

# OCR 引擎初始化较重（加载 ONNX 模型），进程内单例懒加载，多次抽取复用
_OCR_ENGINE = None


def _get_ocr_engine():
    """懒加载并缓存 RapidOCR 引擎实例。"""
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        _OCR_ENGINE = RapidOCR()
    return _OCR_ENGINE


class PDFExtractor:
    """PDF 文本抽取器，支持页眉页脚剔除与表格结构化抽取。

    Args:
        remove_headers_footers: 是否启用跨页重复行检测剔除页眉页脚
        extract_tables: 是否将表格抽取为 Markdown 表格（依赖 pdfplumber）
        head_n: 每页前 N 行参与页眉检测
        tail_n: 每页后 N 行参与页脚检测
        repeat_ratio: 跨页重复比例阈值，>= 该比例的页面出现该行即判为页眉/页脚
        min_pages_for_detection: 页数低于此值不做页眉页脚检测
        enable_ocr: 是否对文本层缺失/过少的页启用 OCR 降级（依赖 rapidocr-onnxruntime）
        ocr_min_chars: 单页文本层字符数低于此阈值即触发该页 OCR 补偿
        ocr_dpi: OCR 渲染页面图像的分辨率，越高越准但越慢
    """

    def __init__(
        self,
        remove_headers_footers: bool = True,
        extract_tables: bool = True,
        head_n: int = 2,
        tail_n: int = 2,
        repeat_ratio: float = 0.5,
        min_pages_for_detection: int = 3,
        enable_ocr: bool = True,
        ocr_min_chars: int = 10,
        ocr_dpi: int = 200,
    ):
        self.remove_headers_footers = remove_headers_footers
        self.extract_tables = extract_tables and _HAS_PDFPLUMBER
        self.head_n = head_n
        self.tail_n = tail_n
        self.repeat_ratio = repeat_ratio
        self.min_pages_for_detection = min_pages_for_detection
        self.enable_ocr = enable_ocr and _HAS_OCR
        self.ocr_min_chars = ocr_min_chars
        self.ocr_dpi = ocr_dpi

        if extract_tables and not _HAS_PDFPLUMBER:
            logger.warning("未安装 pdfplumber，表格抽取功能已自动降级为纯文本模式")
        if enable_ocr and not _HAS_OCR:
            logger.warning("未安装 rapidocr-onnxruntime，扫描件 OCR 降级功能已关闭")

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

        # OCR 降级：对文本层缺失/过少的页（扫描件、图片页）逐页补偿
        if self.enable_ocr:
            pages = self._ocr_fill_pages(path, pages)

        if not any(p.strip() for p in pages):
            raise ValueError("PDF 文件未能提取到任何文本（可能为扫描件或纯图片 PDF）")

        # 页眉页脚剔除：基于行级跨页重复
        if self.remove_headers_footers:
            pages = self._strip_headers_footers(pages)

        return "\n\n".join(p for p in pages if p.strip())

    # ---------- 文本抽取（PyMuPDF，兜底通道）----------

    def _extract_text_only(self, path: Path) -> List[str]:
        """纯文本抽取：使用 PyMuPDF（fitz），按页返回。"""
        pages: List[str] = []
        with fitz.open(str(path)) as doc:
            for i, page in enumerate(doc):
                try:
                    text = page.get_text("text") or ""
                except Exception as e:
                    logger.warning(f"PDF 第 {i + 1} 页提取失败，已跳过: {e}")
                    text = ""
                pages.append(text)
        return pages

    # ---------- OCR 降级（rapidocr-onnxruntime 通道）----------

    def _ocr_fill_pages(self, path: Path, pages: List[str]) -> List[str]:
        """对文本层不足的页用 OCR 重新识别，逐页替换。

        仅渲染并识别 strip 后字符数 < ocr_min_chars 的页，
        纯数字原生 PDF 不会触发任何 OCR 开销。
        """
        need_ocr = [
            i for i, p in enumerate(pages) if len((p or "").strip()) < self.ocr_min_chars
        ]
        if not need_ocr:
            return pages

        logger.info(f"检测到 {len(need_ocr)} 个文本不足页，启用 OCR 识别")
        try:
            engine = _get_ocr_engine()
        except Exception as e:  # pragma: no cover
            logger.warning(f"OCR 引擎初始化失败，跳过 OCR 降级: {e}")
            return pages

        with fitz.open(str(path)) as doc:
            for i in need_ocr:
                try:
                    ocr_text = self._ocr_single_page(engine, doc[i])
                except Exception as e:
                    logger.warning(f"PDF 第 {i + 1} 页 OCR 失败，已跳过: {e}")
                    continue
                if ocr_text.strip():
                    pages[i] = ocr_text
        return pages

    def _ocr_single_page(self, engine, page) -> str:
        """渲染单页为图像并 OCR，返回拼接后的识别文本。"""
        pix = page.get_pixmap(dpi=self.ocr_dpi, alpha=False)
        png_bytes = pix.tobytes("png")
        result, _ = engine(png_bytes)
        if not result:
            return ""
        # RapidOCR 结果每项为 [box, text, score]，按识别顺序拼接为行
        return "\n".join(line[1] for line in result if len(line) >= 2)

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
