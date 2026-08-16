"""真实运行 Chinese-CLIP 小规模图片召回评测。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluations.visual_retrieval.evaluator import (  # noqa: E402
    evaluate_rankings,
    load_dataset,
)
from evaluations.visual_retrieval.generate_dataset import generate  # noqa: E402
from infra.visual import ChineseCLIPEmbedder  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path(__file__).parent
        / "datasets"
        / "synthetic_v1"
        / "visual_retrieval_eval_v1.json",
    )
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    if not args.dataset.exists():
        generate(args.dataset.parent)
    if not args.live:
        print(
            json.dumps(
                {
                    "status": "ready",
                    "dataset": str(args.dataset),
                    "note": "使用 --live 才会下载/调用 Chinese-CLIP",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    dataset = load_dataset(args.dataset)
    images_root = args.dataset.parent / dataset.images_dir
    asset_ids = sorted(path.stem for path in images_root.glob("*.png"))
    image_bytes = [(images_root / f"{asset_id}.png").read_bytes() for asset_id in asset_ids]
    embedder = ChineseCLIPEmbedder()
    image_vectors = embedder.embed_images(image_bytes)
    rankings: dict[str, list[str]] = {}
    for query in dataset.queries:
        query_vector = embedder.embed_texts([query.query])[0]
        scored = sorted(
            (
                (
                    asset_id,
                    sum(
                        left * right
                        for left, right in zip(
                            query_vector,
                            image_vector,
                            strict=True,
                        )
                    ),
                )
                for asset_id, image_vector in zip(
                    asset_ids,
                    image_vectors,
                    strict=True,
                )
            ),
            key=lambda item: item[1],
            reverse=True,
        )
        rankings[query.query_id] = [asset_id for asset_id, _ in scored]
    report = evaluate_rankings(dataset, rankings)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
