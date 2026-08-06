"""页内证据切块器：不跨越页边界。"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from domain.document_content import DocumentParseSnapshot
from domain.documents import CaseDocument, Document, DocumentVersion
from domain.evidence import EvidenceChunk
from processing.splitter import TextSplitter


class PageEvidenceChunker:
    """每页独立切分，保证每个证据块都能定位到稳定页码。"""

    def __init__(
        self,
        *,
        chunk_size: int,
        chunk_overlap: int,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], float],
    ) -> None:
        self._splitter = TextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        self._id_factory = id_factory or (lambda: f"evc_{uuid.uuid4().hex[:16]}")
        self._clock = clock

    def chunk(
        self,
        document: Document,
        version: DocumentVersion,
        snapshot: DocumentParseSnapshot,
        bindings: list[CaseDocument],
    ) -> list[EvidenceChunk]:
        if version.document_id != document.document_id:
            raise ValueError("DocumentVersion 必须属于 Document")
        if snapshot.document_version_id != version.version_id:
            raise ValueError("解析快照必须属于 DocumentVersion")
        if not bindings:
            raise ValueError("文档必须至少绑定一个 Case")
        if any(binding.document_id != document.document_id for binding in bindings):
            raise ValueError("CaseDocument 必须绑定当前 Document")

        created_at = self._clock()
        chunks: list[EvidenceChunk] = []
        for binding in bindings:
            chunk_index = 0
            for page in snapshot.pages:
                page_parts = [page.text.strip()]
                page_parts.extend(table.markdown.strip() for table in page.tables)
                page_text = "\n\n".join(part for part in page_parts if part)
                if not page_text:
                    continue
                for piece in self._splitter.split(page_text):
                    chunks.append(
                        EvidenceChunk(
                            chunk_id=self._id_factory(),
                            workspace_id=document.workspace_id,
                            case_id=binding.case_id,
                            document_id=document.document_id,
                            document_version_id=version.version_id,
                            page_number=page.page_number,
                            chunk_index=chunk_index,
                            text=piece,
                            source_sha256=version.sha256,
                            created_at=created_at,
                        )
                    )
                    chunk_index += 1
        if not chunks:
            raise ValueError("解析快照没有可索引文本")
        return chunks
