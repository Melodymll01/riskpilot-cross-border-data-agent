"""真实执行 independent_llm_v1，并生成可复现的 Evidence QA 评测报告。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluations.evidence_qa.evaluator import (  # noqa: E402
    build_verifier_predictions,
    evaluate,
    load_dataset,
    write_report,
)
from evaluations.evidence_qa.run import DEFAULT_DATASET, DEFAULT_REPORT_DIR  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="使用当前生产 Chat 配置实测 independent_llm_v1",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="确认允许调用真实模型服务并产生费用",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
        help="固定 Claim/Citation 评测数据集",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_REPORT_DIR,
        help="评测报告和 predictions 输出目录",
    )
    args = parser.parse_args(argv)
    if not args.live:
        parser.error("真实验证器评测必须显式传入 --live")

    from config import settings
    from infra.chat import OpenAIChatAdapter
    from infra.qa import StructuredClaimSupportVerifier

    dataset = load_dataset(args.dataset)
    verifier = StructuredClaimSupportVerifier(OpenAIChatAdapter())
    predictions = build_verifier_predictions(
        dataset,
        verifier,
        system=f"independent_llm_v1:{settings.effective_chat_model}",
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    predictions_path = args.output_dir / f"evidence_qa_predictions_{timestamp}.json"
    predictions_path.write_text(
        json.dumps(predictions.model_dump(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    report = evaluate(dataset, predictions)
    archived_json, latest_json, latest_markdown = write_report(report, args.output_dir)
    print(f"候选预测: {predictions_path}")
    print(f"归档报告: {archived_json}")
    print(f"最新 JSON: {latest_json}")
    print(f"最新 Markdown: {latest_markdown}")
    print(f"independent_llm_v1 门禁: {'PASS' if report['passed'] else 'FAIL'}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
