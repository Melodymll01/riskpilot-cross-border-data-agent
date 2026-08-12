"""运行 AI 长期记忆提取协议自检。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluations.memory_extraction.evaluator import (  # noqa: E402
    evaluate_protocol,
    load_dataset,
)

DEFAULT_DATASET = Path(__file__).parent / "datasets" / "memory_extraction_eval_v1.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="验证 AI 长期记忆候选的来源过滤、逐字接地和敏感信息门禁",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
        help="评测数据集 JSON",
    )
    args = parser.parse_args(argv)
    report = evaluate_protocol(load_dataset(args.dataset))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
