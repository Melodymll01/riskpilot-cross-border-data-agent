"""运行 V3 Evidence QA Claim-Citation 离线评测。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluations.evidence_qa.evaluator import (  # noqa: E402
    build_oracle_predictions,
    evaluate,
    load_dataset,
    load_predictions,
    write_report,
)

DEFAULT_DATASET = Path(__file__).parent / "datasets" / "claim_citation_eval_v1.json"
DEFAULT_REPORT_DIR = Path(__file__).parent / "reports"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="评估 V3 Evidence QA 的结构覆盖、语义支持和安全拒答",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
        help="评测数据集 JSON",
    )
    parser.add_argument(
        "--predictions",
        type=Path,
        help="候选系统预测 JSON；正式评测必须提供",
    )
    parser.add_argument(
        "--oracle-self-check",
        action="store_true",
        help="使用 gold 标签回放，仅验证评测协议，不代表生产模型效果",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_REPORT_DIR,
        help="评测报告输出目录",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="只打印结果，不写入 reports/",
    )
    args = parser.parse_args(argv)
    if bool(args.predictions) == args.oracle_self_check:
        parser.error("--predictions 与 --oracle-self-check 必须且只能选择一个")

    dataset = load_dataset(args.dataset)
    predictions = (
        build_oracle_predictions(dataset)
        if args.oracle_self_check
        else load_predictions(args.predictions)
    )
    report = evaluate(dataset, predictions)
    if args.no_write:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        archived_json, latest_json, latest_markdown = write_report(
            report,
            args.output_dir,
        )
        print(f"归档报告: {archived_json}")
        print(f"最新 JSON: {latest_json}")
        print(f"最新 Markdown: {latest_markdown}")
        print(f"Evidence QA 门禁: {'PASS' if report['passed'] else 'FAIL'}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
