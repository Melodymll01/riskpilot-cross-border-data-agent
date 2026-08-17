"""图片召回评测协议测试，不下载模型。"""

from __future__ import annotations

from evaluations.visual_retrieval.evaluator import (
    evaluate_rankings,
    load_dataset,
)
from evaluations.visual_retrieval.generate_dataset import generate
from evaluations.visual_retrieval.run import main


def test_generate_small_synthetic_dataset(tmp_path) -> None:
    dataset_path = generate(tmp_path)
    dataset = load_dataset(dataset_path)

    assert dataset.synthetic is True
    assert len(dataset.queries) == 12
    assert len(list((tmp_path / "images").glob("*.png"))) == 12


def test_perfect_ranking_passes(tmp_path) -> None:
    dataset = load_dataset(generate(tmp_path))
    rankings = {query.query_id: [*query.relevant_asset_ids] for query in dataset.queries}

    report = evaluate_rankings(dataset, rankings)

    assert report["passed"] is True
    assert report["metrics"] == {"recall_at_1": 1.0, "recall_at_3": 1.0}


def test_non_live_cli_does_not_load_model(tmp_path, capsys) -> None:
    dataset_path = generate(tmp_path)

    assert main(["--dataset", str(dataset_path)]) == 0
    assert '"status": "ready"' in capsys.readouterr().out
