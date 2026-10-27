"""PDF 版面抽取与知识库页级元数据回归测试。"""

from __future__ import annotations

from pathlib import Path

import fitz

from ingestion.file_loader import FileLoader
from ingestion.pdf_extractor import PDFExtractor
from processing.metadata import build_chunks


def _draw_table(page: fitz.Page) -> None:
    xs = [72, 200, 330]
    ys = [180, 220, 260]
    for x in xs:
        page.draw_line((x, ys[0]), (x, ys[-1]), color=(0, 0, 0), width=1)
    for y in ys:
        page.draw_line((xs[0], y), (xs[-1], y), color=(0, 0, 0), width=1)
    for x, y, text in (
        (80, 205, "Field"),
        (210, 205, "Value"),
        (80, 245, "Region"),
        (210, 245, "EU"),
    ):
        page.insert_text((x, y), text, fontsize=10)


def _sample_pdf(path: Path) -> None:
    document = fitz.open()
    for page_number in range(1, 4):
        page = document.new_page()
        page.insert_text((72, 36), "CONFIDENTIAL")
        page.insert_text((72, 100), f"body-{page_number}")
        if page_number == 2:
            _draw_table(page)
        page.insert_text((300, 810), str(page_number))
    document.save(path)
    document.close()


def test_pdf_extractor_removes_edge_noise_and_keeps_table(tmp_path: Path) -> None:
    path = tmp_path / "guide.pdf"
    _sample_pdf(path)

    result = PDFExtractor(enable_ocr=False).extract_pages(path)

    assert result.removed_edge_lines == 6
    assert result.table_count == 1
    assert result.metadata["pdf_table_pages"] == "2"
    assert [page.text for page in result.pages] == ["body-1", "body-2", "body-3"]
    assert result.pages[1].content_kind == "table"
    assert "| Field | Value |" in result.pages[1].tables[0].markdown
    assert "CONFIDENTIAL" not in result.content


def test_file_loader_builds_page_scoped_chunks(tmp_path: Path) -> None:
    path = tmp_path / "guide.pdf"
    _sample_pdf(path)

    raw = FileLoader(pdf_extractor=PDFExtractor(enable_ocr=False)).load(str(path))
    chunks = build_chunks(raw)

    assert len(raw.segments) == 3
    assert raw.extra_metadata["pdf_page_count"] == "3"
    assert {chunk.source_metadata["page_number"] for chunk in chunks} == {"1", "2", "3"}
    page_two = [chunk for chunk in chunks if chunk.source_metadata["page_number"] == "2"]
    assert page_two
    assert all(chunk.source_metadata["content_kind"] == "table" for chunk in page_two)
    assert any("| Region | EU |" in chunk.text for chunk in page_two)
