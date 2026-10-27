import hashlib
import json
import sys
from types import SimpleNamespace

import pytest

from app.factories import build_document_loader
from config import Settings
from infra.agents.langchain_copilot import _chunk_to_evidence
from infra.search.hybrid_retriever import _dict_to_chunk
from ingestion.regulatory_corpus import prepare_corpus
from scripts.build_regulatory_kb import main


def config(**kwargs):
    return Settings(_env_file=None, embed_provider="sentence_transformer", **kwargs)


def manifest(tmp_path):
    source = tmp_path / "law.txt"
    source.write_text(
        "第十九条 个人信息保存期限应为实现处理目的所必要的最短时间。", encoding="utf-8"
    )
    data = {
        "schema_version": 1,
        "corpus_id": "test",
        "scope": "测试摘录",
        "documents": [
            {
                "document_id": "law-v1",
                "path": "law.txt",
                "title": "法规摘录",
                "source_url": "https://example.test/law",
                "version": "v1",
                "jurisdiction": "CN",
                "clause_id": "第十九条",
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            }
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data, ensure_ascii=False))
    return path, data


def test_sources_and_parameters_reach_retrieval_citations(tmp_path):
    path, data = manifest(tmp_path)
    first = prepare_corpus(path, config(chunk_size=400, chunk_overlap=80))
    second = prepare_corpus(path, config(chunk_size=400, chunk_overlap=80))
    assert first[2][0].chunk_id == second[2][0].chunk_id
    assert first[4]["chunk_size"] == 400 and first[4]["chunk_overlap"] == 80
    assert first[4]["embedding_max_input_tokens"] == 480
    chunk = first[2][0]
    retrieved = _dict_to_chunk(
        {"id": chunk.chunk_id, "text": chunk.text, "metadata": chunk.to_metadata_dict()}
    )
    citation = _chunk_to_evidence(retrieved, corpus="regulatory")
    assert citation.source_version == "v1"
    assert citation.source_sha256 == data["documents"][0]["sha256"]
    assert citation.source_url == data["documents"][0]["source_url"]
    assert citation.clause_id == "第十九条"
    assert retrieved.owner_id is None


@pytest.mark.parametrize("mutation", ["sha", "path", "duplicate"])
def test_invalid_manifest_never_builds_index(tmp_path, mutation):
    path, data = manifest(tmp_path)
    if mutation == "sha":
        data["documents"][0]["sha256"] = "0" * 64
    elif mutation == "path":
        data["documents"][0]["path"] = "../outside.txt"
    else:
        data["documents"].append(data["documents"][0].copy())
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        prepare_corpus(path, config())


def test_prepare_only_freezes_sources_and_refuses_overwrite(tmp_path, monkeypatch):
    path, _ = manifest(tmp_path)
    output = tmp_path / "build"
    monkeypatch.setenv("EMBED_PROVIDER", "sentence_transformer")
    monkeypatch.setattr(
        sys,
        "argv",
        ["build_regulatory_kb", "--manifest", str(path), "--output", str(output), "--prepare-only"],
    )
    main()
    report = json.loads((output / "build_report.json").read_text())
    assert report["status"] == "prepared" and not report["ready_for_query"]
    assert (output / "sources/law-v1.txt").read_bytes() == (tmp_path / "law.txt").read_bytes()
    assert not (output / "chroma").exists()
    with pytest.raises(SystemExit) as raised:
        main()
    assert raised.value.code == 2


def test_loader_uses_requested_chunk_parameters(tmp_path):
    path = tmp_path / "long.txt"
    path.write_text("甲" * 600)
    loader = build_document_loader(config(chunk_size=100, chunk_overlap=0))
    chunks = loader.load_file(str(path))
    assert len(chunks) == 6
    assert all(len(chunk.text) == 100 for chunk in chunks)


def test_local_only_model_loading_cannot_download(monkeypatch):
    from infra.search import local_embedder

    calls = []
    monkeypatch.setattr(local_embedder, "_MODELS", {})
    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(
            SentenceTransformer=lambda *args, **kwargs: calls.append(kwargs) or object(),
        ),
    )
    local_embedder.LocalSentenceEmbedder("test-model", local_files_only=True)._model()
    assert calls[0]["local_files_only"] is True


def test_failed_embedding_does_not_mark_build_ready(tmp_path, monkeypatch):
    from infra.search import local_embedder
    from retrieval.search import vector_store

    path, _ = manifest(tmp_path)
    output = tmp_path / "failed-build"
    monkeypatch.setenv("EMBED_PROVIDER", "sentence_transformer")
    monkeypatch.setattr(
        local_embedder,
        "LocalSentenceEmbedder",
        lambda *a, **k: SimpleNamespace(embed_texts=lambda texts: [[0.0]]),
    )
    monkeypatch.setattr(vector_store, "VectorStore", lambda **k: object())
    monkeypatch.setattr(
        sys, "argv", ["build_regulatory_kb", "--manifest", str(path), "--output", str(output)]
    )
    with pytest.raises(ValueError, match="零向量"):
        main()
    report = json.loads((output / "build_report.json").read_text())
    assert report["status"] == "failed" and not report["ready_for_query"]
