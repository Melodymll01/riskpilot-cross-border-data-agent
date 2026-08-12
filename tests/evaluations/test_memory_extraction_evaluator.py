"""AI 长期记忆提取协议评测测试。"""

from __future__ import annotations

from evaluations.memory_extraction.evaluator import (
    evaluate_protocol,
    load_dataset,
)
from evaluations.memory_extraction.run import DEFAULT_DATASET, main


def test_dataset_covers_required_risk_categories() -> None:
    dataset = load_dataset(DEFAULT_DATASET)

    categories = {case.category for case in dataset.cases}
    assert {
        "explicit_preference",
        "stable_business_context",
        "assistant_pollution",
        "forged_quote",
        "prompt_injection",
        "api_secret",
        "personal_identifier",
        "sensitive_inference",
        "temporary_request",
    } <= categories


def test_protocol_self_check_passes_all_gates() -> None:
    report = evaluate_protocol(load_dataset(DEFAULT_DATASET))

    assert report["passed"] is True
    assert report["production_model_evidence"] is False
    assert report["metrics"] == {
        "decision_accuracy": 1.0,
        "unsafe_false_accept_count": 0,
        "source_filter_accuracy": 1.0,
    }


def test_cli_returns_success(capsys) -> None:
    assert main([]) == 0
    output = capsys.readouterr().out
    assert '"mode": "protocol_self_check"' in output
    assert '"passed": true' in output
