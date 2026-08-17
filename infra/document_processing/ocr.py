"""RapidOCR 文档页补全 Adapter。"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import fitz

from domain.document_content import DocumentParseSnapshot, ParsedPage
from domain.documents import DocumentVersion
from domain.errors import InvalidDocumentContent, UnsupportedDocumentType

OCR_PARSER_VERSION = "rapidocr-onnxruntime-v1"


class RapidOcrDocumentAdapter:
    """只 OCR PDF 中解析阶段标记为 empty 的页，模型首次调用时才加载。"""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
        engine: Any | None = None,
        zoom: float = 2.0,
    ) -> None:
        if zoom <= 0:
            raise ValueError("zoom 必须大于 0")
        self._clock = clock
        self._engine_instance = engine
        self._zoom = zoom

    def apply_ocr(
        self,
        version: DocumentVersion,
        content: bytes,
        snapshot: DocumentParseSnapshot,
    ) -> DocumentParseSnapshot:
        if snapshot.document_version_id != version.version_id:
            raise ValueError("OCR Snapshot 必须属于当前 DocumentVersion")
        if snapshot.source_sha256 != version.sha256:
            raise InvalidDocumentContent("OCR 输入快照与 DocumentVersion SHA-256 不一致")
        if version.mime_type != "application/pdf":
            raise UnsupportedDocumentType("OCR 当前只支持 PDF")
        empty_pages = {
            page.page_number for page in snapshot.pages if page.extraction_method == "empty"
        }
        if not empty_pages:
            return snapshot

        try:
            document = fitz.open(stream=content, filetype="pdf")
        except Exception as exc:
            raise InvalidDocumentContent("OCR PDF 无法打开") from exc
        updated_pages: list[ParsedPage] = []
        try:
            for page in snapshot.pages:
                if page.page_number not in empty_pages:
                    updated_pages.append(page)
                    continue
                pixmap = document.load_page(page.page_number - 1).get_pixmap(
                    matrix=fitz.Matrix(self._zoom, self._zoom),
                    alpha=False,
                )
                result, _elapsed = self._engine()(pixmap.tobytes("png"))
                lines, confidence = _normalize_ocr_result(result)
                if not lines:
                    raise InvalidDocumentContent(f"第 {page.page_number} 页 OCR 未识别到正文")
                updated_pages.append(
                    ParsedPage(
                        page_number=page.page_number,
                        text="\n".join(lines),
                        extraction_method="ocr",
                        ocr_confidence=confidence,
                        tables=page.tables,
                        warnings=[*page.warnings, "已使用 OCR 补全文本"],
                    )
                )
        finally:
            document.close()
        return snapshot.model_copy(
            update={
                "parser_version": f"{snapshot.parser_version}+{OCR_PARSER_VERSION}",
                "pages": updated_pages,
                "warnings": [*snapshot.warnings, "空文本页已通过 OCR 补全"],
                "parsed_at": max(self._clock(), snapshot.parsed_at),
            }
        )

    def _engine(self) -> Any:
        if self._engine_instance is None:
            from rapidocr_onnxruntime import RapidOCR

            self._engine_instance = RapidOCR()
        return self._engine_instance


def _normalize_ocr_result(result: Any) -> tuple[list[str], float]:
    if not isinstance(result, list):
        return [], 0.0
    lines: list[str] = []
    confidences: list[float] = []
    for item in result:
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            continue
        text = str(item[1]).strip()
        if not text:
            continue
        lines.append(text)
        try:
            confidences.append(max(0.0, min(1.0, float(item[2]))))
        except (TypeError, ValueError):
            confidences.append(0.0)
    confidence = sum(confidences) / len(confidences) if confidences else 0.0
    return lines, confidence
