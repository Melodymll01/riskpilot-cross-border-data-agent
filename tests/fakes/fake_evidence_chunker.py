"""EvidenceChunkerPort Fake。"""

from __future__ import annotations

from domain.document_content import DocumentParseSnapshot
from domain.documents import CaseDocument, Document, DocumentVersion
from domain.evidence import EvidenceChunk


class FakeEvidenceChunker:
    def chunk(
        self,
        document: Document,
        version: DocumentVersion,
        snapshot: DocumentParseSnapshot,
        bindings: list[CaseDocument],
    ) -> list[EvidenceChunk]:
        return [
            EvidenceChunk(
                chunk_id=f"evc_{version.version_id}_{binding.case_id}",
                workspace_id=document.workspace_id,
                case_id=binding.case_id,
                document_id=document.document_id,
                document_version_id=version.version_id,
                page_number=1,
                chunk_index=0,
                text=snapshot.pages[0].text,
                source_sha256=version.sha256,
                created_at=snapshot.parsed_at,
            )
            for binding in bindings
        ]
