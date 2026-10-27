"""Internal document processing without a Python business catalog."""

from __future__ import annotations

import base64
import hashlib
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

from domain.document_content import DocumentParseSnapshot, ParsedPage
from domain.documents import DocumentVersion
from domain.errors import UnsupportedDocumentType
from domain.models import KbChunk
from domain.runtime import DocumentProcess, DocumentResult
from processing.metadata import PUBLIC_OWNER_MARKER

if TYPE_CHECKING:
    from domain.ports import (
        DocumentLoaderPort,
        DocumentParserPort,
        EmbedPort,
        EvidenceChunkerPort,
        EvidenceIndexPort,
        KbDocumentRepoPort,
        ParseArtifactStorePort,
    )


class InternalDocumentService:
    def __init__(
        self,
        *,
        loader: DocumentLoaderPort,
        parser: DocumentParserPort,
        chunker: EvidenceChunkerPort,
        embedder: EmbedPort,
        kb_repo: KbDocumentRepoPort,
        parse_artifacts: ParseArtifactStorePort,
        evidence_index: EvidenceIndexPort,
    ) -> None:
        self._loader = loader
        self._parser = parser
        self._chunker = chunker
        self._embedder = embedder
        self._kb_repo = kb_repo
        self._parse_artifacts = parse_artifacts
        self._evidence_index = evidence_index

    def process(self, command: DocumentProcess) -> DocumentResult:
        try:
            if command.operation == "delete":
                return self._delete(command)
            return self._ingest(command)
        except Exception as exc:
            return DocumentResult(
                document_id=command.document_id,
                status="failed",
                title=command.source_name,
                chunk_count=0,
                error_code=type(exc).__name__,
                message="document processing failed closed",
            )

    def _ingest(self, command: DocumentProcess) -> DocumentResult:
        owner_scope = command.owner_id or PUBLIC_OWNER_MARKER
        version = DocumentVersion(
            document_id=command.document_id,
            version_id=command.version_id,
            sha256=command.sha256,
            mime_type=command.mime_type,
        )
        if command.source_type == "file":
            assert command.content_base64 is not None
            content = base64.b64decode(command.content_base64, validate=True)
            if hashlib.sha256(content).hexdigest() != command.sha256:
                raise ValueError("file content SHA-256 does not match immutable command")
            kb_chunks = self._load_file(command, content)
            try:
                snapshot = self._parser.parse(version, content)
            except UnsupportedDocumentType:
                snapshot = _logical_snapshot(command, kb_chunks)
        else:
            assert command.source_url is not None
            if hashlib.sha256(command.source_url.encode()).hexdigest() != command.sha256:
                raise ValueError("web URL SHA-256 does not match immutable command")
            kb_chunks = self._loader.load_web(
                command.source_url,
                category=command.category,
                owner_id=command.owner_id,
            )
            snapshot = _logical_snapshot(command, kb_chunks)
        if not kb_chunks:
            raise ValueError("document produced no indexable content")

        kb_chunks = [
            item.model_copy(
                update={
                    "source_name": command.source_name,
                    "source_url": command.source_url,
                    "category": command.category,
                    "owner_id": command.owner_id,
                    "source_metadata": {
                        **item.source_metadata,
                        "document_id": command.document_id,
                        "document_version_id": command.version_id,
                        "source_sha256": command.sha256,
                    },
                }
            )
            for item in kb_chunks
        ]
        kb_embeddings = self._embedder.embed(
            [item.embedding_text or item.text for item in kb_chunks]
        )

        self._parse_artifacts.save(
            owner_id=owner_scope,
            document_id=command.document_id,
            document_version_id=command.version_id,
            source_sha256=command.sha256,
            snapshot=snapshot,
        )
        evidence_chunks = self._chunker.chunk(
            owner_scope,
            command.document_id,
            version,
            snapshot,
        )
        evidence_embeddings = self._embedder.embed([item.text for item in evidence_chunks])
        try:
            self._evidence_index.replace_version_chunks(
                command.version_id,
                evidence_chunks,
                evidence_embeddings,
            )
            self._kb_repo.add_chunks(kb_chunks, kb_embeddings)
        except Exception:
            self._evidence_index.delete_version(command.version_id)
            self._parse_artifacts.delete(
                owner_id=owner_scope,
                document_id=command.document_id,
                document_version_id=command.version_id,
                source_sha256=command.sha256,
            )
            raise

        title = kb_chunks[0].title or command.source_name
        return DocumentResult(
            document_id=command.document_id,
            status="ready",
            title=title,
            chunk_count=len(kb_chunks),
            error_code=None,
            message="document artifacts ready",
        )

    def _delete(self, command: DocumentProcess) -> DocumentResult:
        owner_scope = command.owner_id or PUBLIC_OWNER_MARKER
        deleted_chunks = self._kb_repo.delete_document(
            command.source_name,
            owner_id=command.owner_id,
        )
        self._evidence_index.delete_version(command.version_id)
        self._parse_artifacts.delete(
            owner_id=owner_scope,
            document_id=command.document_id,
            document_version_id=command.version_id,
            source_sha256=command.sha256,
        )
        return DocumentResult(
            document_id=command.document_id,
            status="deleted",
            title=command.source_name,
            chunk_count=deleted_chunks,
            error_code=None,
            message="document artifacts deleted",
        )

    def _load_file(self, command: DocumentProcess, content: bytes) -> list[KbChunk]:
        suffix = Path(command.source_name).suffix
        with tempfile.NamedTemporaryFile(suffix=suffix) as temporary:
            temporary.write(content)
            temporary.flush()
            return self._loader.load_file(
                temporary.name,
                original_filename=command.source_name,
                category=command.category,
                owner_id=command.owner_id,
            )


def _logical_snapshot(
    command: DocumentProcess,
    chunks: list[KbChunk],
) -> DocumentParseSnapshot:
    text = "\n\n".join(item.text for item in chunks if item.text.strip())
    if not text:
        raise ValueError("document produced no parseable text")
    return DocumentParseSnapshot(
        snapshot_id=f"parse_{command.version_id}",
        document_version_id=command.version_id,
        parser_name="riskpilot-unified-loader",
        parser_version="1.0",
        source_sha256=command.sha256,
        pages=[
            ParsedPage(
                page_number=1,
                text=text,
                extraction_method="native",
                warnings=["logical page derived from normalized ingestion chunks"],
            )
        ],
        warnings=["source does not expose stable physical pages"],
        parsed_at=time.time(),
    )
