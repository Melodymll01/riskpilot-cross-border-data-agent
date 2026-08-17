"""运行 Case Assessment Agent 轨迹评测。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agent_tools.case_assessment import (  # noqa: E402
    CASE_ASSESSMENT_TOOL_SCHEMA_VERSION,
)
from evaluations.agent_runs.evaluator import (  # noqa: E402
    AGENT_RUN_EVALUATOR_VERSION,
    evaluate,
    load_dataset,
    write_report,
)
from evaluations.agent_runs.executor import execute_scenario  # noqa: E402
from evaluations.agent_runs.models import AgentPredictions  # noqa: E402
from infra.agents.evidence_planner import (  # noqa: E402
    EVIDENCE_PLAN_PROMPT_VERSION,
)

DEFAULT_DATASET = Path(__file__).parent / "datasets" / "agent_runs_eval_v1.json"
DEFAULT_REPORT_DIR = Path(__file__).parent / "reports"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="执行 RiskPilot Case Assessment Agent 完整轨迹评测",
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument(
        "--live",
        action="store_true",
        help="使用当前生产模型执行 Planner；会访问模型服务并产生费用",
    )
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)
    run_live = os.getenv("RUN_LIVE")
    if run_live not in {None, "", "1"}:
        parser.error("RUN_LIVE 必须为空或等于 1")
    live = args.live or run_live == "1"

    dataset = load_dataset(args.dataset)
    expected_versions = {
        "prompt_version": EVIDENCE_PLAN_PROMPT_VERSION,
        "tool_schema_version": CASE_ASSESSMENT_TOOL_SCHEMA_VERSION,
        "evaluator_version": AGENT_RUN_EVALUATOR_VERSION,
    }
    for field_name, expected in expected_versions.items():
        if getattr(dataset, field_name) != expected:
            parser.error(f"{field_name} 与当前代码版本不一致: expected={expected}")
    planner = None
    mode = "offline"
    model_version = "deterministic-evidence-planner-v1"
    cost: float | None = 0.0
    if live:
        from app.factories import build_agent_model
        from config import settings
        from infra.agents import LangChainEvidencePlanner

        settings.validate_runtime_configuration()
        planner = LangChainEvidencePlanner(build_agent_model(settings))
        mode = "live"
        model_version = settings.effective_chat_model
        cost = None

    predictions = []
    with tempfile.TemporaryDirectory(prefix="riskpilot-agent-eval-") as temp_dir:
        root = Path(temp_dir)
        for case_ref in dataset.cases:
            scenario = dataset.expand_scenario(case_ref)
            predictions.append(
                execute_scenario(
                    case_ref.case_id,
                    scenario,
                    checkpoint_path=root / f"{case_ref.case_id}.sqlite3",
                    planner=planner,
                    cost=cost,
                )
            )
    prediction_bundle = AgentPredictions(
        dataset_name=dataset.name,
        dataset_version=dataset.version,
        mode=mode,
        system="riskpilot-case-assessment-agent",
        model_version=model_version,
        prompt_version=dataset.prompt_version,
        tool_schema_version=dataset.tool_schema_version,
        evaluator_version=dataset.evaluator_version,
        cases=predictions,
    )
    report = evaluate(dataset, prediction_bundle)
    if args.no_write:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        archived, latest_json, latest_markdown = write_report(
            report,
            args.output_dir,
        )
        print(f"归档报告: {archived}")
        print(f"最新 JSON: {latest_json}")
        print(f"最新 Markdown: {latest_markdown}")
        print(f"Agent Run Eval: {'PASS' if report['passed'] else 'FAIL'}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
