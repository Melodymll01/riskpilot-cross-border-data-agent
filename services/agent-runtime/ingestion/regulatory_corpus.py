"""Prepare a local, source-attributed regulatory corpus with frozen parameters."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from config import Settings
from ingestion.file_loader import FileLoader
from ingestion.unified_loader import RawDocument
from processing.cleaner import TextCleaner
from processing.metadata import build_chunks
from processing.splitter import TextSplitter


class RegulatorySource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
    path: str = Field(min_length=1)
    title: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    version: str = Field(min_length=1, max_length=200)
    jurisdiction: str = Field(min_length=1)
    clause_id: str | None = None
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_url(self):
        url = urlsplit(self.source_url)
        if url.scheme not in {"http", "https"} or not url.hostname:
            raise ValueError("source_url 必须是实际来源的 HTTP(S) 地址")
        return self


class RegulatoryManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    corpus_id: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    documents: list[RegulatorySource] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_documents(self):
        ids = [item.document_id for item in self.documents]
        if len(ids) != len(set(ids)):
            raise ValueError("document_id 必须唯一；不同版本使用不同的 document_id")
        return self


def prepare_corpus(manifest_path: Path, settings: Settings):
    manifest = RegulatoryManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    if not 0 <= settings.chunk_overlap < settings.chunk_size:
        raise ValueError("chunk_overlap 必须小于 chunk_size")
    if settings.embed_provider != "sentence_transformer":
        raise ValueError("离线建库要求 EMBED_PROVIDER=sentence_transformer")
    root = manifest_path.resolve().parent
    splitter = TextSplitter(settings.chunk_size, settings.chunk_overlap)
    cleaner, loader = TextCleaner(), FileLoader()
    chunks, sources, snapshots = [], [], {}
    for item in manifest.documents:
        path = (root / item.path).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"法规文件必须位于清单所在目录内: {item.document_id}")
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if item.sha256 is not None and digest != item.sha256:
            raise ValueError(f"法规文件 SHA256 不匹配: {item.document_id}")
        metadata = {
            "source_version": item.version,
            "source_sha256": digest,
            "legal_document_id": item.document_id,
            "jurisdiction": item.jurisdiction,
            "corpus_id": manifest.corpus_id,
        }
        if item.clause_id:
            metadata["clause_id"] = item.clause_id

        if path.suffix.lower() == ".json":
            text = json.loads(data)["text"]
            if not isinstance(text, str):
                raise ValueError("JSON 法规文件必须包含字符串 text")
            raw = RawDocument(
                content=text,
                source_type="file",
                source_name=item.document_id,
                title=item.title,
                source_url=item.source_url,
                extra_metadata=metadata,
            )
        elif path.suffix.lower() in {".txt", ".md", ".markdown"}:
            text = data.decode("utf-8-sig")
            raw = RawDocument(
                content=text,
                source_type="file",
                source_name=item.document_id,
                title=item.title,
                source_url=item.source_url,
                extra_metadata=metadata,
            )
        else:
            loaded = loader.load(str(path), original_filename=path.name)
            raw = RawDocument(
                content=loaded.content,
                source_type="file",
                source_name=item.document_id,
                title=item.title,
                source_url=item.source_url,
                extra_metadata={**loaded.extra_metadata, **metadata},
                segments=loaded.segments,
            )
        prepared = build_chunks(raw, cleaner=cleaner, splitter=splitter)
        if not prepared:
            raise ValueError(f"法规没有可入库正文: {item.document_id}")
        for index, chunk in enumerate(prepared):
            identity = (
                f"{manifest.corpus_id}:{item.document_id}:{digest}:"
                f"{settings.embedding_identity}:{index}:{chunk.text}"
            )
            chunk.chunk_id = "law_" + hashlib.sha256(identity.encode()).hexdigest()[:32]
            chunk.chunk_index = index
            chunk.category = "法规"
            chunks.append(chunk)
        snapshot_name = f"{item.document_id}{path.suffix.lower()}"
        snapshots[snapshot_name] = data
        sources.append(
            {
                **item.model_dump(),
                "sha256": digest,
                "snapshot": "sources/" + snapshot_name,
                "chunk_count": len(prepared),
            }
        )
    params = {
        key: getattr(settings, key)
        for key in (
            "chunk_size",
            "chunk_overlap",
            "top_k",
            "enable_query_rewrite",
            "enable_reranker",
            "enable_bm25_rrf",
            "rrf_k",
            "rrf_vector_weight",
            "rrf_bm25_weight",
            "context_window_size",
            "distance_threshold",
        )
    }
    params.update(
        embedding_model=settings.local_sentence_embedding_model,
        embedding_identity=settings.embedding_identity,
        embedding_max_input_tokens=settings.embedding_max_input_tokens,
        chunk_length_unit="characters",
        pipeline_version="regulatory_corpus_parent_child_v1",
    )
    return manifest, sources, chunks, snapshots, params


def write_prepared(output: Path, sources, chunks, snapshots, params, manifest) -> None:
    output.mkdir(parents=True, exist_ok=False)
    (output / "sources").mkdir()
    for name, data in snapshots.items():
        (output / "sources" / name).write_bytes(data)
    (output / "chunks.jsonl").write_text(
        "".join(json.dumps(asdict(chunk), ensure_ascii=False) + "\n" for chunk in chunks),
        encoding="utf-8",
    )
    (output / "manifest.lock.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "corpus_id": manifest.corpus_id,
                "scope": manifest.scope,
                "parameters": params,
                "documents": sources,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
