"""V2 页级文档解析产物测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from domain import DocumentParseSnapshot, ParsedPage, ParsedTable

_SHA256 = "a" * 64


def _page(page_number: int, text: str = "正文") -> ParsedPage:
    return ParsedPage(
        page_number=page_number,
        text=text,
        extraction_method="native",
    )


def _snapshot(*pages: ParsedPage, **overrides: object) -> DocumentParseSnapshot:
    values: dict[str, object] = {
        "snapshot_id": "parse_001",
        "document_version_id": "ver_001",
        "parser_name": "riskpilot-parser",
        "parser_version": "1.0.0",
        "source_sha256": _SHA256,
        "pages": list(pages) or [_page(1)],
        "parsed_at": 100.0,
    }
    values.update(overrides)
    return DocumentParseSnapshot(**values)  # type: ignore[arg-type]


class TestParsedTable:
    def test_happy_path(self) -> None:
        table = ParsedTable(
            table_id="table_001",
            page_number=1,
            markdown="| 字段 | 值 |\n| --- | --- |\n| 地区 | EU |",
            row_count=2,
            column_count=2,
        )
        assert table.row_count == 2

    def test_blank_markdown_rejected(self) -> None:
        with pytest.raises(ValidationError, match="markdown"):
            ParsedTable(
                table_id="table_001",
                page_number=1,
                markdown=" ",
                row_count=1,
                column_count=1,
            )


class TestParsedPage:
    def test_native_page(self) -> None:
        page = _page(1)
        assert page.ocr_confidence is None
        assert page.tables == []

    def test_ocr_page(self) -> None:
        page = ParsedPage(
            page_number=2,
            text="OCR 正文",
            extraction_method="ocr",
            ocr_confidence=0.91,
        )
        assert page.ocr_confidence == 0.91

    def test_empty_page(self) -> None:
        page = ParsedPage(
            page_number=1,
            extraction_method="empty",
            warnings=["未识别到文本"],
        )
        assert page.text == ""

    def test_empty_page_cannot_have_text(self) -> None:
        with pytest.raises(ValidationError, match="empty"):
            ParsedPage(
                page_number=1,
                text="不应存在",
                extraction_method="empty",
            )

    def test_native_page_cannot_have_ocr_confidence(self) -> None:
        with pytest.raises(ValidationError, match="OCR"):
            ParsedPage(
                page_number=1,
                text="正文",
                extraction_method="native",
                ocr_confidence=0.9,
            )

    def test_table_page_must_match(self) -> None:
        with pytest.raises(ValidationError, match="页码"):
            ParsedPage(
                page_number=1,
                text="正文",
                extraction_method="native",
                tables=[
                    ParsedTable(
                        table_id="t1",
                        page_number=2,
                        markdown="| a |\n| --- |",
                        row_count=1,
                        column_count=1,
                    )
                ],
            )


class TestDocumentParseSnapshot:
    def test_metrics_and_render_text(self) -> None:
        table = ParsedTable(
            table_id="t1",
            page_number=2,
            markdown="| a |\n| --- |\n| b |",
            row_count=2,
            column_count=1,
        )
        snapshot = _snapshot(
            _page(1, "第一页"),
            ParsedPage(
                page_number=2,
                text="第二页",
                extraction_method="native",
                tables=[table],
            ),
        )
        assert snapshot.page_count == 2
        assert snapshot.text_char_count == 6
        rendered = snapshot.render_text()
        assert "<!-- page:1 -->" in rendered
        assert "<!-- page:2 -->" in rendered
        assert "| a |" in rendered

    @pytest.mark.parametrize(
        "pages",
        [
            [_page(2)],
            [_page(1), _page(3)],
            [_page(2), _page(1)],
        ],
    )
    def test_page_numbers_must_be_contiguous(self, pages: list[ParsedPage]) -> None:
        with pytest.raises(ValidationError, match="连续页码"):
            _snapshot(*pages)

    def test_table_ids_must_be_globally_unique(self) -> None:
        def page_with_table(page_number: int) -> ParsedPage:
            return ParsedPage(
                page_number=page_number,
                text="正文",
                extraction_method="native",
                tables=[
                    ParsedTable(
                        table_id="same",
                        page_number=page_number,
                        markdown="| a |\n| --- |",
                        row_count=1,
                        column_count=1,
                    )
                ],
            )

        with pytest.raises(ValidationError, match="全局唯一"):
            _snapshot(page_with_table(1), page_with_table(2))

    def test_invalid_source_hash_rejected(self) -> None:
        with pytest.raises(ValidationError, match="source_sha256"):
            _snapshot(source_sha256="A" * 64)

    def test_json_round_trip_preserves_computed_fields(self) -> None:
        snapshot = _snapshot(_page(1))
        restored = DocumentParseSnapshot.model_validate_json(snapshot.model_dump_json())
        assert restored == snapshot
        assert restored.page_count == 1
