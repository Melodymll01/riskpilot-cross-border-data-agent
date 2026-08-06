"""V2 案件证据索引模型测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from domain import EvidenceChunk, EvidenceSearchHit


def _chunk(**overrides: object) -> EvidenceChunk:
    values: dict[str, object] = {
        "chunk_id": "evc_001",
        "workspace_id": "ws_001",
        "case_id": "case_001",
        "document_id": "doc_001",
        "document_version_id": "ver_001",
        "page_number": 1,
        "chunk_index": 0,
        "text": "境外接收方应承担安全保护责任",
        "source_sha256": "a" * 64,
        "created_at": 100.0,
    }
    values.update(overrides)
    return EvidenceChunk(**values)  # type: ignore[arg-type]


class TestEvidenceChunk:
    def test_happy_path(self) -> None:
        chunk = _chunk()
        assert chunk.case_id == "case_001"
        assert chunk.page_number == 1

    def test_scope_fields_required(self) -> None:
        with pytest.raises(ValidationError):
            _chunk(case_id="")

    def test_blank_text_rejected(self) -> None:
        with pytest.raises(ValidationError, match="text"):
            _chunk(text=" ")

    def test_invalid_source_hash_rejected(self) -> None:
        with pytest.raises(ValidationError, match="source_sha256"):
            _chunk(source_sha256="A" * 64)

    def test_json_round_trip(self) -> None:
        chunk = _chunk()
        assert EvidenceChunk.model_validate_json(chunk.model_dump_json()) == chunk


class TestEvidenceSearchHit:
    def test_happy_path(self) -> None:
        hit = EvidenceSearchHit(
            chunk=_chunk(),
            score=0.03,
            vector_score=0.8,
            bm25_score=1.2,
        )
        assert hit.chunk.document_id == "doc_001"

    def test_invalid_vector_score_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EvidenceSearchHit(
                chunk=_chunk(),
                score=0.03,
                vector_score=1.2,
                bm25_score=0.0,
            )
