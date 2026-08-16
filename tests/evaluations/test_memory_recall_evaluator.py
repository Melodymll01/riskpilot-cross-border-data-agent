"""AI 长期记忆召回排序协议评测测试。"""

from __future__ import annotations

from evaluations.memory_recall.evaluator import evaluate_protocol, load_dataset
from evaluations.memory_recall.run import DEFAULT_DATASET, main


def test_dataset_covers_recall_quality_and_safety_categories() -> None:
    dataset = load_dataset(DEFAULT_DATASET)

    categories = {case.category for case in dataset.cases}
    assert {
        "semantic_relevance",
        "trust_aware_rerank",
        "low_relevance_rejection",
        "superseded_filter",
        "ttl_filter",
        "owner_isolation",
    } <= categories


def test_protocol_self_check_passes_all_gates() -> None:
    report = evaluate_protocol(load_dataset(DEFAULT_DATASET))

    assert report["passed"] is True
    assert report["strategy_version"] == "hybrid_v1"
    assert report["production_embedding_evidence"] is False
    assert report["metrics"] == {
        "hit_rate_at_k": 1.0,
        "top1_accuracy": 1.0,
        "forbidden_recall_count": 0,
        "irrelevant_recall_rate": 0.0,
    }


def test_cli_returns_success(capsys) -> None:
    assert main([]) == 0
    output = capsys.readouterr().out
    assert '"strategy_version": "hybrid_v1"' in output
    assert '"passed": true' in output
