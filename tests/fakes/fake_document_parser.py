"""DocumentParserPort Fake。"""

from __future__ import annotations

from domain.document_content import DocumentParseSnapshot, ParsedPage
from domain.documents import DocumentVersion


class FakeDocumentParser:
    def __init__(self, *, raise_error: Exception | None = None) -> None:
        self.raise_error = raise_error
        self.calls: list[tuple[DocumentVersion, bytes]] = []

    def parse(
        self,
        version: DocumentVersion,
        content: bytes,
    ) -> DocumentParseSnapshot:
        self.calls.append((version, content))
        if self.raise_error is not None:
            raise self.raise_error
        return DocumentParseSnapshot(
            snapshot_id=f"parse_{version.version_id}",
            document_version_id=version.version_id,
            parser_name="fake-parser",
            parser_version="test",
            source_sha256=version.sha256,
            pages=[
                ParsedPage(
                    page_number=1,
                    text=content.decode("utf-8", errors="replace"),
                    extraction_method="native",
                )
            ],
            parsed_at=101.0,
        )
