"""DocumentOcrPort Fake。"""

from __future__ import annotations

from domain.document_content import DocumentParseSnapshot, ParsedPage
from domain.documents import DocumentVersion


class FakeDocumentOcr:
    def __init__(self, *, raise_error: Exception | None = None) -> None:
        self.raise_error = raise_error
        self.calls: list[str] = []

    def apply_ocr(
        self,
        version: DocumentVersion,
        content: bytes,
        snapshot: DocumentParseSnapshot,
    ) -> DocumentParseSnapshot:
        self.calls.append(version.version_id)
        if self.raise_error is not None:
            raise self.raise_error
        pages = [
            (
                ParsedPage(
                    page_number=page.page_number,
                    text=f"OCR 第 {page.page_number} 页",
                    extraction_method="ocr",
                    ocr_confidence=0.9,
                    tables=page.tables,
                    warnings=[*page.warnings, "fake ocr"],
                )
                if page.extraction_method == "empty"
                else page
            )
            for page in snapshot.pages
        ]
        return snapshot.model_copy(
            update={
                "parser_version": f"{snapshot.parser_version}+fake-ocr",
                "pages": pages,
                "parsed_at": snapshot.parsed_at + 1,
            }
        )
