"""Prepare/build a local regulatory knowledge base before serving questions."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="新的产物目录，不能覆盖已有目录")
    parser.add_argument(
        "--prepare-only", action="store_true", help="只校验、切分和保存原文，不加载向量模型"
    )
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 256:
        parser.error("batch-size 必须为 1 至 256")
    if args.output.exists():
        parser.error("output 已存在，请为本次建库选择新的目录")
    # Set before importing model libraries; this command never invokes an LLM.
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", ANONYMIZED_TELEMETRY="False")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from config import Settings
    from ingestion.regulatory_corpus import prepare_corpus, write_prepared

    settings = Settings()
    manifest, sources, chunks, snapshots, params = prepare_corpus(args.manifest, settings)
    write_prepared(args.output, sources, chunks, snapshots, params, manifest)
    report = {
        "corpus_id": manifest.corpus_id,
        "scope": manifest.scope,
        "document_count": len(sources),
        "chunk_count": len(chunks),
        "parameters": params,
        "ready_for_query": False,
        "status": "prepared",
        "offline": True,
    }
    report_path = args.output / "build_report.json"

    def save_report():
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    save_report()
    if not args.prepare_only:
        try:
            from infra.search.local_embedder import LocalSentenceEmbedder
            from retrieval.search.vector_store import VectorStore

            embedder = LocalSentenceEmbedder(
                settings.local_sentence_embedding_model, local_files_only=True
            )
            store = VectorStore(
                config=settings.model_copy(
                    update={"chroma_persist_dir": str(args.output / "chroma")}
                )
            )
            dimension = None
            for start in range(0, len(chunks), args.batch_size):
                batch = chunks[start : start + args.batch_size]
                vectors = embedder.embed_texts(
                    [chunk.embedding_text or chunk.text for chunk in batch]
                )
                if len(vectors) != len(batch):
                    raise ValueError("向量数与文本块数量不一致")
                for vector in vectors:
                    if (
                        not vector
                        or not all(math.isfinite(x) for x in vector)
                        or sum(x * x for x in vector) <= 0
                    ):
                        raise ValueError("向量为空、非有限数或零向量，拒绝入库")
                    dimension = dimension or len(vector)
                    if len(vector) != dimension:
                        raise ValueError("向量维度不一致")
                store.add_chunks(batch, vectors)
                print(
                    json.dumps(
                        {"indexed": min(start + len(batch), len(chunks)), "total": len(chunks)}
                    ),
                    flush=True,
                )
            if store.collection.count() != len(chunks):
                raise ValueError("建库数量核验失败")
            report.update(
                status="completed",
                ready_for_query=True,
                actual_embedding_dimensions=dimension,
                chroma_directory=str((args.output / "chroma").resolve()),
            )
        except Exception as exc:
            report.update(status="failed", error_type=type(exc).__name__)
            save_report()
            raise
        save_report()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
