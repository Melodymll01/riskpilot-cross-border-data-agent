"""V3 Evidence QA 离线评测器测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from domain.qa import (
    ClaimSupportJudgement,
    ClaimSupportResult,
    EvidenceQAClaim,
)
from evaluations.evidence_qa.evaluator import (
    EvidenceQACasePrediction,
    EvidenceQAEvaluationCase,
    EvidenceQAPredictions,
    GoldClaimSupport,
    build_oracle_predictions,
    build_verifier_predictions,
    evaluate,
    load_dataset,
    write_report,
)
from evaluations.evidence_qa.run import DEFAULT_DATASET, main
from evaluations.evidence_qa.run_verifier import main as verifier_main


@pytest.fixture
def dataset():
    return load_dataset(DEFAULT_DATASET)


def test_dataset_covers_required_risk_categories(dataset) -> None:
    categories = {case.category for case in dataset.cases}
    assert {
        "direct_support",
        "partial_answer",
        "negation_entailment",
        "numeric_overclaim",
        "uncited_claim",
        "forged_citation",
        "citation_drift",
        "cross_workspace_isolation",
        "cross_case_isolation",
        "no_evidence_refusal",
    }.issubset(categories)
    assert len(dataset.cases) == 14


def test_oracle_self_check_passes_all_gates(dataset) -> None:
    report = evaluate(dataset, build_oracle_predictions(dataset))

    assert report["passed"] is True
    assert report["candidate"]["production_evidence"] is False
    assert report["metrics"] == {
        "case_count": 14,
        "claim_count": 14,
        "structural_accuracy": 1.0,
        "supported_claim_recall": 1.0,
        "unsupported_claim_false_accept_rate": 0.0,
        "claim_filter_accuracy": 1.0,
        "citation_drift_recall": 1.0,
        "status_accuracy": 1.0,
        "cross_scope_leakage_count": 0,
        "verifier_error_count": 0,
    }


def test_unsupported_claim_false_acceptance_fails_gate(dataset) -> None:
    predictions = build_oracle_predictions(dataset)
    target = next(prediction for prediction in predictions.cases if prediction.case_id == "EQA-005")
    target.judgements = [
        ClaimSupportJudgement(
            claim_id="C1",
            supported=True,
            citation_ids=["E1"],
        )
    ]

    report = evaluate(dataset, predictions)

    assert report["passed"] is False
    assert report["metrics"]["unsupported_claim_false_accept_rate"] == pytest.approx(0.2)
    assert report["gates"]["unsupported_claim_false_accept_rate"]["passed"] is False


def test_cross_workspace_answer_counts_as_leakage(dataset) -> None:
    predictions = build_oracle_predictions(dataset)
    target = next(prediction for prediction in predictions.cases if prediction.case_id == "EQA-009")
    target.status = "answered"
    target.kept_claim_ids = ["C1"]

    report = evaluate(dataset, predictions)

    assert report["metrics"]["cross_scope_leakage_count"] == 1
    assert report["gates"]["cross_scope_leakage_count"]["passed"] is False
    assert report["gates"]["claim_filter_accuracy"]["passed"] is False


def test_supported_claim_removed_by_candidate_fails_filter_gate(dataset) -> None:
    predictions = build_oracle_predictions(dataset)
    target = next(prediction for prediction in predictions.cases if prediction.case_id == "EQA-001")
    target.status = "refused"
    target.kept_claim_ids = []

    report = evaluate(dataset, predictions)

    assert report["metrics"]["claim_filter_accuracy"] == pytest.approx(13 / 14)
    assert report["gates"]["claim_filter_accuracy"]["passed"] is False


def test_mixed_claim_case_keeps_only_safe_claim(dataset) -> None:
    safe_case = dataset.cases[0]
    mixed_case = EvidenceQAEvaluationCase.model_validate(
        safe_case.model_copy(
            update={
                "case_id": "EQA-SYNTHETIC-MIXED",
                "claims": [
                    *safe_case.claims,
                    EvidenceQAClaim(
                        claim_id="C2",
                        text="无引用结论。",
                        citation_ids=[],
                    ),
                ],
                "gold": safe_case.gold.model_copy(
                    update={
                        "expected_status": "partially_answered",
                        "expected_structural_valid": False,
                        "claim_support": {
                            **safe_case.gold.claim_support,
                            "C2": GoldClaimSupport(supported=False),
                        },
                    }
                ),
            }
        ).model_dump()
    )
    synthetic_dataset = dataset.model_copy(update={"cases": [mixed_case]})

    prediction = build_oracle_predictions(synthetic_dataset).cases[0]

    assert prediction.status == "partially_answered"
    assert prediction.kept_claim_ids == ["C1"]


def test_live_verifier_predictions_use_only_claims_and_citations(dataset) -> None:
    safe_case = dataset.cases[0]
    mixed_case = EvidenceQAEvaluationCase.model_validate(
        safe_case.model_copy(
            update={
                "case_id": "EQA-SYNTHETIC-LIVE",
                "claims": [
                    *safe_case.claims,
                    EvidenceQAClaim(
                        claim_id="C2",
                        text="原文不支持的结论。",
                        citation_ids=["E1"],
                    ),
                ],
                "gold": safe_case.gold.model_copy(
                    update={
                        "expected_status": "partially_answered",
                        "claim_support": {
                            **safe_case.gold.claim_support,
                            "C2": GoldClaimSupport(supported=False),
                        },
                    }
                ),
            }
        ).model_dump()
    )
    synthetic_dataset = dataset.model_copy(update={"cases": [mixed_case]})

    class RecordingVerifier:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def verify(self, claims, citations):  # type: ignore[no-untyped-def]
            self.calls.append({"claims": claims, "citations": citations})
            return ClaimSupportResult(
                judgements=[
                    ClaimSupportJudgement(
                        claim_id="C1",
                        supported=True,
                        citation_ids=["E1"],
                    ),
                    ClaimSupportJudgement(
                        claim_id="C2",
                        supported=False,
                        citation_ids=[],
                        reason="原文不支持",
                    ),
                ],
                unsupported_claim_ids=["C2"],
                valid=False,
            )

    verifier = RecordingVerifier()
    predictions = build_verifier_predictions(
        synthetic_dataset,
        verifier,
        system="independent_llm_v1:test-model",
    )

    assert predictions.mode == "production_verifier"
    assert predictions.cases[0].status == "partially_answered"
    assert predictions.cases[0].kept_claim_ids == ["C1"]
    assert list(verifier.calls[0]) == ["claims", "citations"]
    report = evaluate(synthetic_dataset, predictions)
    assert report["passed"] is True
    assert report["candidate"]["evaluated_component"] == "independent_llm_v1"
    assert report["gates"]["status_accuracy"]["applicable"] is False


def test_live_verifier_error_is_recorded_and_fails_gate(dataset) -> None:
    class IncompleteVerifier:
        def verify(self, claims, citations):  # type: ignore[no-untyped-def]
            raise RuntimeError("provider unavailable " + "x" * 2000)

    synthetic_dataset = dataset.model_copy(update={"cases": [dataset.cases[0]]})
    predictions = build_verifier_predictions(
        synthetic_dataset,
        IncompleteVerifier(),
        system="broken",
    )

    assert predictions.cases[0].status == "refused"
    assert predictions.cases[0].error is not None
    assert len(predictions.cases[0].error) == 1000
    report = evaluate(synthetic_dataset, predictions)
    assert report["metrics"]["verifier_error_count"] == 1
    assert report["gates"]["verifier_error_count"]["passed"] is False
    assert report["passed"] is False


def test_prediction_must_cover_every_claim(dataset) -> None:
    predictions = build_oracle_predictions(dataset)
    predictions.cases[0].judgements = []

    with pytest.raises(ValueError, match="judgements"):
        evaluate(dataset, predictions)


def test_prediction_dataset_version_must_match(dataset) -> None:
    predictions = build_oracle_predictions(dataset).model_copy(update={"dataset_version": "999.0"})

    with pytest.raises(ValueError, match="dataset_version"):
        evaluate(dataset, predictions)


def test_write_report_marks_oracle_as_non_production_evidence(
    dataset,
    tmp_path: Path,
) -> None:
    report = evaluate(dataset, build_oracle_predictions(dataset))

    archived_json, latest_json, latest_markdown = write_report(report, tmp_path)

    assert archived_json.exists()
    assert latest_json.exists()
    assert json.loads(latest_json.read_text(encoding="utf-8"))["passed"] is True
    markdown = latest_markdown.read_text(encoding="utf-8")
    assert "总门禁：**PASS**" in markdown
    assert "不能作为生产模型效果或安全性证据" in markdown


def test_cli_oracle_self_check_without_writing() -> None:
    assert main(["--oracle-self-check", "--no-write"]) == 0


def test_cli_requires_exactly_one_candidate_mode() -> None:
    with pytest.raises(SystemExit):
        main(["--no-write"])


def test_live_verifier_cli_requires_explicit_live_flag() -> None:
    with pytest.raises(SystemExit):
        verifier_main([])


def test_prediction_schema_rejects_duplicate_cases(dataset) -> None:
    prediction = build_oracle_predictions(dataset).cases[0]
    with pytest.raises(ValueError, match="case_id"):
        EvidenceQAPredictions(
            dataset_name=dataset.name,
            dataset_version=dataset.version,
            system="candidate",
            cases=[prediction, prediction],
        )


def test_prediction_schema_rejects_duplicate_judgements() -> None:
    judgement = ClaimSupportJudgement(
        claim_id="C1",
        supported=True,
        citation_ids=["E1"],
    )
    with pytest.raises(ValueError, match="claim_id"):
        EvidenceQACasePrediction(
            case_id="case-1",
            status="answered",
            judgements=[judgement, judgement],
            kept_claim_ids=["C1"],
        )
