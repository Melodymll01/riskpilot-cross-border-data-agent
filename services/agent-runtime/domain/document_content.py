"""V2 文档页级解析产物模型。"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, model_validator

from domain.models import BaseDomainModel

ExtractionMethod = Literal["native", "ocr", "mixed", "empty"]
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ParsedTable(BaseDomainModel):
    """单页内抽取出的表格。"""

    table_id: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    markdown: str = Field(min_length=1)
    row_count: int = Field(ge=1)
    column_count: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_table(self) -> ParsedTable:
        if not self.markdown.strip():
            raise ValueError("markdown 不能为空白字符串")
        return self


class ParsedPage(BaseDomainModel):
    """稳定页码对应的正文、表格和抽取来源。"""

    page_number: int = Field(ge=1)
    text: str = ""
    extraction_method: ExtractionMethod
    ocr_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    tables: list[ParsedTable] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_page(self) -> ParsedPage:
        has_text = bool(self.text.strip())
        if self.extraction_method == "empty" and has_text:
            raise ValueError("empty 页面不能包含正文")
        if self.extraction_method != "empty" and not has_text and not self.tables:
            raise ValueError("非 empty 页面必须包含正文或表格")
        if self.extraction_method == "native" and self.ocr_confidence is not None:
            raise ValueError("native 页面不能携带 OCR 置信度")
        if any(table.page_number != self.page_number for table in self.tables):
            raise ValueError("表格页码必须与所属页面一致")
        table_ids = [table.table_id for table in self.tables]
        if len(table_ids) != len(set(table_ids)):
            raise ValueError("同一页面的 table_id 不能重复")
        return self


class DocumentParseSnapshot(BaseDomainModel):
    """某个 DocumentVersion 的不可变页级解析快照。"""

    snapshot_id: str = Field(min_length=1)
    document_version_id: str = Field(min_length=1)
    parser_name: str = Field(min_length=1, max_length=100)
    parser_version: str = Field(min_length=1, max_length=100)
    source_sha256: str = Field(min_length=64, max_length=64)
    pages: list[ParsedPage] = Field(min_length=1)
    warnings: list[str] = Field(default_factory=list)
    parsed_at: float

    @model_validator(mode="after")
    def validate_snapshot(self) -> DocumentParseSnapshot:
        if not _SHA256_RE.fullmatch(self.source_sha256):
            raise ValueError("source_sha256 必须是 64 位小写十六进制")
        page_numbers = [page.page_number for page in self.pages]
        expected = list(range(1, len(self.pages) + 1))
        if page_numbers != expected:
            raise ValueError("pages 必须按从 1 开始的连续页码排序")
        table_ids = [table.table_id for page in self.pages for table in page.tables]
        if len(table_ids) != len(set(table_ids)):
            raise ValueError("快照内的 table_id 必须全局唯一")
        return self

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def text_char_count(self) -> int:
        return sum(len(page.text) for page in self.pages)

    def render_text(self) -> str:
        """按稳定页码渲染文本，供后续切块器消费。"""
        sections: list[str] = []
        for page in self.pages:
            page_parts = [page.text.strip()]
            page_parts.extend(table.markdown.strip() for table in page.tables)
            body = "\n\n".join(part for part in page_parts if part)
            sections.append(f"<!-- page:{page.page_number} -->\n{body}".rstrip())
        return "\n\n".join(sections)
