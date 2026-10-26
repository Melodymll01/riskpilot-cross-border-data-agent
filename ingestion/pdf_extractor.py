"""PDF 抽取器：在 PyMuPDF 文本层抽取基础上叠加页眉页脚剔除、表格结构化抽取、扫描页 OCR 降级。

设计原则：
- 单一职责：只负责 PDF -> 文本，不关心上层业务
- 可配置：通过构造参数开关各项增强能力，便于按数据源差异化使用
- 渐进降级：表格抽取依赖 pdfplumber；文本层缺失页回退到 RapidOCR 本地识别，依赖未装时自动关闭
"""

import io
import logging
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

PDFSource = Path | bytes


@dataclass(frozen=True)
class ExtractedPDFTable:
    """PDF 单页中保留下来的结构化表格。"""

    markdown: str
    row_count: int
    column_count: int


@dataclass(frozen=True)
class ExtractedPDFPage:
    """版面感知的单页抽取结果，供知识库与附件链路共同复用。"""

    page_number: int
    text: str
    tables: tuple[ExtractedPDFTable, ...] = ()
    extraction_method: str = "native"
    content_kind: str = "body"
    visual_content_detected: bool = False

    @property
    def content(self) -> str:
        parts = [self.text.strip()]
        parts.extend(table.markdown.strip() for table in self.tables)
        return "\n\n".join(part for part in parts if part)


@dataclass(frozen=True)
class PDFExtractionResult:
    """整份 PDF 的页级结果与可审计质量信息。"""

    pages: tuple[ExtractedPDFPage, ...]
    removed_edge_lines: int = 0

    @property
    def content(self) -> str:
        return "\n\n".join(page.content for page in self.pages if page.content)

    @property
    def table_count(self) -> int:
        return sum(len(page.tables) for page in self.pages)

    @property
    def metadata(self) -> dict[str, str]:
        table_pages = [str(page.page_number) for page in self.pages if page.tables]
        visual_pages = [
            str(page.page_number) for page in self.pages if page.visual_content_detected
        ]
        return {
            "extraction_method": "pdf_layout",
            "extracted_text_chars": str(len(self.content)),
            "pdf_page_count": str(len(self.pages)),
            "pdf_table_count": str(self.table_count),
            "pdf_table_pages": ",".join(table_pages),
            "pdf_visual_pages": ",".join(visual_pages),
            "pdf_removed_edge_lines": str(self.removed_edge_lines),
        }


try:
    import pdfplumber

    _HAS_PDFPLUMBER = True
except ImportError:  # pragma: no cover
    _HAS_PDFPLUMBER = False

try:
    from rapidocr_onnxruntime import RapidOCR

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

    def extract(self, source: PDFSource) -> str:
        """抽取 PDF 全部文本。

        Returns:
            清洗后的文本，页之间用两个换行分隔
        """
        result = self.extract_pages(source)
        if not result.content:
            raise ValueError("PDF 文件未能提取到任何文本（可能为扫描件或纯图片 PDF）")
        return result.content

    def extract_pages(self, source: PDFSource) -> PDFExtractionResult:
        """按页抽取正文与表格，同时保留页码、内容类型和视觉内容提示。"""
        if self.extract_tables:
            raw_pages, page_texts, page_tables, visual_flags = self._extract_with_table_details(
                source
            )
        else:
            raw_pages = self._extract_text_only(source)
            page_texts = list(raw_pages)
            page_tables = [[] for _ in page_texts]
            visual_flags = self._detect_visual_pages(source, len(page_texts))

        extraction_methods = ["native" for _ in page_texts]
        if self.enable_ocr:
            need_ocr = [
                index
                for index, text in enumerate(page_texts)
                if len(text.strip()) < self.ocr_min_chars and not page_tables[index]
            ]
            page_texts = self._ocr_fill_pages(source, page_texts, indexes=need_ocr)
            for index in need_ocr:
                if page_texts[index].strip():
                    extraction_methods[index] = "ocr"

        removed_count = 0
        if self.remove_headers_footers:
            repeated = self._find_repeated_edge_patterns(raw_pages)
            page_texts, removed_count = self._remove_repeated_patterns(page_texts, repeated)

        template_context = False
        pages: list[ExtractedPDFPage] = []
        for index, text in enumerate(page_texts):
            tables = tuple(page_tables[index])
            template_context = self._next_template_context(text, template_context)
            content_kind = self._classify_page(
                text,
                tables=tables,
                is_template=template_context,
                has_visual=visual_flags[index],
            )
            pages.append(
                ExtractedPDFPage(
                    page_number=index + 1,
                    text=text.strip(),
                    tables=tables,
                    extraction_method=extraction_methods[index],
                    content_kind=content_kind,
                    visual_content_detected=visual_flags[index],
                )
            )

        return PDFExtractionResult(tuple(pages), removed_edge_lines=removed_count)

    # ---------- 文本抽取（PyMuPDF，兜底通道）----------

    def _extract_text_only(self, source: PDFSource) -> list[str]:
        """纯文本抽取：使用 PyMuPDF（fitz），按页返回。"""
        pages: list[str] = []
        with self._open_fitz(source) as doc:
            for i, page in enumerate(doc):
                try:
                    text = page.get_text("text") or ""
                except Exception as e:
                    logger.warning(f"PDF 第 {i + 1} 页提取失败，已跳过: {e}")
                    text = ""
                pages.append(text)
        return pages

    @staticmethod
    def _open_fitz(source: PDFSource):
        if isinstance(source, bytes):
            return fitz.open(stream=source, filetype="pdf")
        return fitz.open(str(source))

    @staticmethod
    def _pdfplumber_source(source: PDFSource):
        if isinstance(source, bytes):
            return io.BytesIO(source)
        return str(source)

    # ---------- OCR 降级（rapidocr-onnxruntime 通道）----------

    def _ocr_fill_pages(
        self,
        source: PDFSource,
        pages: list[str],
        *,
        indexes: list[int] | None = None,
    ) -> list[str]:
        """对文本层不足的页用 OCR 重新识别，逐页替换。

        仅渲染并识别 strip 后字符数 < ocr_min_chars 的页，
        纯数字原生 PDF 不会触发任何 OCR 开销。
        """
        need_ocr = indexes
        if need_ocr is None:
            need_ocr = [
                i
                for i, page_text in enumerate(pages)
                if len(page_text.strip()) < self.ocr_min_chars
            ]
        if not need_ocr:
            return pages

        logger.info(f"检测到 {len(need_ocr)} 个文本不足页，启用 OCR 识别")
        try:
            engine = _get_ocr_engine()
        except Exception as e:  # pragma: no cover
            logger.warning(f"OCR 引擎初始化失败，跳过 OCR 降级: {e}")
            return pages

        with self._open_fitz(source) as doc:
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

    def _extract_with_tables(self, source: PDFSource) -> list[str]:
        """文本 + 表格抽取：表格区域单独序列化为 Markdown，正文排除该区域。"""
        _, page_texts, page_tables, _ = self._extract_with_table_details(source)
        pages: list[str] = []
        for text, tables in zip(page_texts, page_tables, strict=True):
            parts = [text.strip()]
            parts.extend(table.markdown for table in tables)
            pages.append("\n\n".join(part for part in parts if part))
        return pages

    def _extract_with_table_details(
        self, source: PDFSource
    ) -> tuple[list[str], list[str], list[list[ExtractedPDFTable]], list[bool]]:
        """同时返回原始页文本、去表格正文、结构化表格与视觉内容候选。"""
        raw_pages: list[str] = []
        page_texts: list[str] = []
        page_tables: list[list[ExtractedPDFTable]] = []
        visual_flags: list[bool] = []
        with pdfplumber.open(self._pdfplumber_source(source)) as pdf:
            for i, page in enumerate(pdf.pages):
                try:
                    raw_text = page.extract_text() or ""
                except Exception as e:
                    logger.warning(f"PDF 第 {i + 1} 页原始文本抽取失败: {e}")
                    raw_text = ""
                try:
                    page_text, tables = self._extract_single_page_table_details(page)
                except Exception as e:
                    logger.warning(f"PDF 第 {i + 1} 页版面抽取失败，回退纯文本: {e}")
                    page_text = raw_text
                    tables = []
                raw_pages.append(raw_text)
                page_texts.append(page_text)
                page_tables.append(tables)
                visual_flags.append(self._is_visual_page(page, raw_text, bool(tables)))
        return raw_pages, page_texts, page_tables, visual_flags

    def _extract_single_page_with_tables(self, page) -> str:
        """单页：抽取表格 bbox，正文 filter 排除表格区域，最后拼接。"""
        non_table_text, tables = self._extract_single_page_table_details(page)
        parts = [non_table_text.strip()]
        parts.extend(table.markdown for table in tables)
        return "\n\n".join(part for part in parts if part)

    def _extract_single_page_table_details(self, page) -> tuple[str, list[ExtractedPDFTable]]:
        """单页抽取，正文与表格分开返回，避免后续再次丢失二维结构。"""
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
        extracted_tables: list[ExtractedPDFTable] = []
        for table in tables:
            try:
                rows = table.extract()
            except Exception:
                continue
            if not rows or len(rows) < 2:
                continue
            md = self._rows_to_markdown(rows)
            if md:
                column_count = max(len(row) for row in rows)
                row_count = sum(1 for row in rows if any(cell for cell in row))
                extracted_tables.append(
                    ExtractedPDFTable(
                        markdown=md,
                        row_count=row_count,
                        column_count=column_count,
                    )
                )

        return non_table_text, extracted_tables

    @staticmethod
    def _is_visual_page(page, raw_text: str, has_tables: bool) -> bool:
        """标记可能包含图示/流程图的页；只做提示，不伪造图形关系。"""
        if getattr(page, "images", None):
            return True
        vector_count = sum(
            len(getattr(page, attr, None) or ()) for attr in ("lines", "curves", "rects")
        )
        return not has_tables and vector_count >= 20 and len(raw_text.strip()) < 240

    def _detect_visual_pages(self, source: PDFSource, page_count: int) -> list[bool]:
        flags = [False] * page_count
        with self._open_fitz(source) as document:
            for index, page in enumerate(document):
                if index >= page_count:
                    break
                try:
                    flags[index] = bool(page.get_images(full=True)) or (
                        len(page.get_drawings()) >= 20
                        and len((page.get_text("text") or "").strip()) < 240
                    )
                except Exception:
                    flags[index] = False
        return flags

    @staticmethod
    def _rows_to_markdown(rows: list[list[str | None]]) -> str:
        """二维行列表 -> Markdown 表格字符串。"""
        # 归一化单元格：None -> ""，去换行
        norm: list[list[str]] = []
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

    def _strip_headers_footers(self, pages: list[str]) -> list[str]:
        """跨页重复行检测，剔除页眉页脚。"""
        repeated = self._find_repeated_edge_patterns(pages)
        cleaned, _ = self._remove_repeated_patterns(pages, repeated)
        return cleaned

    def _find_repeated_edge_patterns(self, pages: list[str]) -> set[str]:
        """先在原始阅读顺序中识别跨页重复的页眉、页脚和页码模式。"""
        if len(pages) < self.min_pages_for_detection:
            return set()

        # 每页拆行
        pages_lines: list[list[str]] = [
            [ln.strip() for ln in p.splitlines() if ln.strip()] for p in pages
        ]

        # 收集每页头尾候选行的归一化形式
        counter: Counter = Counter()
        for lines in pages_lines:
            if not lines:
                continue
            # 短页不能把全部正文都当成边缘候选；最多使用该页行数的四分之一。
            head_count = min(self.head_n, max(1, len(lines) // 4))
            tail_count = min(self.tail_n, max(1, len(lines) // 4))
            candidates = lines[:head_count] + lines[-tail_count:]
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

        return repeated

    def _remove_repeated_patterns(
        self, pages: list[str], repeated: set[str]
    ) -> tuple[list[str], int]:
        """从版面重排后的正文移除已确认边缘模式。

        表格抽取会把 Markdown 表格追加到正文后，原本处于页脚的页码可能因此移动到
        页面文本中间。模式必须先从原始页边缘识别，再按整行精确匹配移除。
        """
        if not repeated:
            return pages, 0

        logger.info(f"检测到 {len(repeated)} 个页眉/页脚模式，将从 {len(pages)} 页中剔除")
        cleaned_pages: list[str] = []
        removed = 0
        for page in pages:
            kept: list[str] = []
            for line in page.splitlines():
                stripped = line.strip()
                if stripped and self._normalize_for_match(stripped) in repeated:
                    removed += 1
                    continue
                kept.append(line)
            cleaned_pages.append("\n".join(kept).strip())
        return cleaned_pages, removed

    @staticmethod
    def _next_template_context(text: str, current: bool) -> bool:
        """附件标题出现时更新是否处于表单/报告模板章节。"""
        leading = "\n".join(line.strip() for line in text.splitlines()[:8])
        if re.search(r"(?m)^附件\s*\d+\s*$", leading):
            return "模板" in leading
        return current

    @staticmethod
    def _classify_page(
        text: str,
        *,
        tables: tuple[ExtractedPDFTable, ...],
        is_template: bool,
        has_visual: bool,
    ) -> str:
        if is_template and tables:
            return "template_table"
        if is_template:
            return "template"
        if tables:
            return "table"
        if has_visual:
            return "visual"
        if len(text.strip()) < 120:
            return "low_information"
        return "body"

    @staticmethod
    def _normalize_for_match(line: str) -> str:
        """归一化用于跨页匹配：把数字替换为占位符，使带页码的行也能聚类。"""
        # 多个连续数字视为同一占位符
        return re.sub(r"\d+", "#", line).strip()
