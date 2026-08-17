"""RapidOcrDocumentAdapter 单元测试，不加载真实 OCR 模型。"""

from __future__ import annotations

import hashlib

import fitz
import pytest

from domain import DocumentOcrPort, DocumentParseSnapshot, DocumentVersion, ParsedPage
from infra.document_processing import RapidOcrDocumentAdapter


class FakeEngine:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, image: bytes):
        self.calls += 1
        assert image.startswith(b"\x89PNG")
        return (
            [
                [[[0, 0], [1, 0], [1, 1], [0, 1]], "出境合同", 0.9],
                [[[0, 2], [1, 2], [1, 3], [0, 3]], "重要数据", 0.8],
            ],
            [0.01, 0.02, 0.03],
        )


def _pdf() -> bytes:
    document = fitz.open()
    document.new_page()
    content = document.tobytes()
    document.close()
    return content


def test_rapid_ocr_adapter_replaces_only_empty_pages() -> None:
    content = _pdf()
    version = DocumentVersion(
        version_id="ver_001",
        document_id="doc_001",
        version_number=1,
        object_key="objects/source.pdf",
        sha256=hashlib.sha256(content).hexdigest(),
        mime_type="application/pdf",
        size_bytes=len(content),
        created_at=100.0,
    )
    snapshot = DocumentParseSnapshot(
        snapshot_id="parse_001",
        document_version_id=version.version_id,
        parser_name="test",
        parser_version="1.0",
        source_sha256=version.sha256,
        pages=[ParsedPage(page_number=1, extraction_method="empty")],
        parsed_at=101.0,
    )
    engine = FakeEngine()
    adapter = RapidOcrDocumentAdapter(clock=lambda: 102.0, engine=engine)

    updated = adapter.apply_ocr(version, content, snapshot)

    assert isinstance(adapter, DocumentOcrPort)
    assert engine.calls == 1
    assert updated.pages[0].text == "出境合同\n重要数据"
    assert updated.pages[0].extraction_method == "ocr"
    assert updated.pages[0].ocr_confidence == pytest.approx(0.85)
    assert updated.parsed_at == 102.0
