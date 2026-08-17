"""Case Assessment Agent 轨迹评测器测试。"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from evaluations.agent_runs.evaluator import evaluate, load_dataset
from evaluations.agent_runs.executor import execute_scenario
from evaluations.agent_runs.models import REQUIRED_CATEGORIES, AgentPredictions
from evaluations.agent_runs.run import DEFAULT_DATASET, main


@pytest.fixture(scope="module")
def dataset():
    return load_dataset(DEFAULT_DATASET)


@pytest.fixture(scope="module")
def predictions(dataset):
    cases = []
    with TemporaryDirectory(prefix="agent-eval-test-") as temp_dir:
        root = Path(temp_dir)
        for case_ref in dataset.cases:
            cases.append(
                execute_scenario(
                    case_ref.case_id,
                    dataset.expand_scenario(case_ref),
                    checkpoint_path=root / f"{case_ref.case_id}.sqlite3",
                )
            )
    return AgentPredictions(
        dataset_name=dataset.name,
        dataset_version=dataset.version,
        mode="offline",
        system="riskpilot-case-assessment-agent",
        model_version="deterministic-evidence-planner-v1",
        prompt_version=dataset.prompt_version,
        tool_schema_version=dataset.tool_schema_version,
        evaluator_version=dataset.evaluator_version,
        cases=cases,
    )


def test_dataset_has_39_cases_and_all_required_categories(dataset) -> None:
    assert len(dataset.cases) == 39
    assert {case.category for case in dataset.cases} == REQUIRED_CATEGORIES
    assert dataset.leakage_control["frozen"] is True


def test_offline_real_graph_eval_passes_all_safety_gates(dataset, predictions) -> None:
    report = evaluate(dataset, predictions)

    assert report["passed"] is True
    metrics = report["metrics"]
    assert metrics["task_success_rate"] == 1.0
    assert metrics["tool_selection_accuracy"] == 1.0
    assert metrics["tool_argument_accuracy"] == 1.0
    assert metrics["missing_fact_recall"] == 1.0
    assert metrics["citation_precision"] == 1.0
    assert metrics["unsupported_claim_false_accept_rate"] == 0.0
    assert metrics["unsafe_action_rate"] == 0.0
    assert metrics["cross_tenant_leakage_rate"] == 0.0
    assert metrics["recovery_success_rate"] == 1.0


def test_runner_prediction_is_independent_from_gold(dataset) -> None:
    case_ref = dataset.cases[0]
    scenario = dataset.expand_scenario(case_ref)
    with TemporaryDirectory(prefix="agent-gold-leak-test-") as temp_dir:
        first = execute_scenario(
            case_ref.case_id,
            scenario,
            checkpoint_path=Path(temp_dir) / "first.sqlite3",
        )
        tampered = dataset.model_copy(deep=True)
        tampered.cases[0].gold_overrides["expected_status"] = "failed"
        tampered_scenario = tampered.expand_scenario(tampered.cases[0])
        second = execute_scenario(
            case_ref.case_id,
            tampered_scenario,
            checkpoint_path=Path(temp_dir) / "second.sqlite3",
        )

    comparable_fields = {
        "status",
        "stage",
        "interrupt_kind",
        "error_type",
        "completed_stages",
        "tool_calls",
        "observed_missing_fact_fields",
        "citations_valid",
        "review_decision",
        "safe_refusal",
        "unsafe_action_blocked",
        "leaked_identifiers",
    }
    assert {field: getattr(first, field) for field in comparable_fields} == {
        field: getattr(second, field) for field in comparable_fields
    }


def test_prediction_version_mismatch_rejected(dataset, predictions) -> None:
    wrong = predictions.model_copy(update={"dataset_version": "999"})

    with pytest.raises(ValueError, match="dataset_version"):
        evaluate(dataset, wrong)


def test_unsafe_action_gate_fails_when_probe_is_not_blocked(dataset, predictions) -> None:
    target = next(case for case in predictions.cases if case.unsafe_action_attempted)
    modified = predictions.model_copy(
        update={
            "cases": [
                case.model_copy(update={"unsafe_action_blocked": False})
                if case.case_id == target.case_id
                else case
                for case in predictions.cases
            ]
        }
    )

    report = evaluate(dataset, modified)

    assert report["passed"] is False
    assert report["metrics"]["unsafe_action_rate"] > 0
    assert report["gates"]["unsafe_action_rate"]["passed"] is False


def test_live_requires_explicit_flag_and_offline_cli_runs_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RUN_LIVE", raising=False)

    assert main(["--no-write"]) == 0
    monkeypatch.setenv("RUN_LIVE", "true")
    with pytest.raises(SystemExit):
        main(["--no-write"])
