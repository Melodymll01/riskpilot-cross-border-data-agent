"""chunk_size / chunk_overlap 参数评估脚本。

用法：
  # 仅评估切分质量（不调 API，不花钱）
  python eval_chunk_params.py

  # 同时评估检索命中率（调 embedding API）
  python eval_chunk_params.py --with-retrieval

  # 指定文档
  python eval_chunk_params.py --file ./data/uploads/数据出境安全申报第三版.pdf
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# 允许从任意 CWD 运行：把项目根加入 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
os.chdir(_PROJECT_ROOT)

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# ── 评估数据 ────────────────────────────────────────────────────────────────
# 默认使用 uploads 目录下第一个文件
DEFAULT_UPLOAD_DIR = "./data/uploads"

# 每个文件对应的 test cases（按文件名关键字匹配）。
# 若文件名包含 dict key，就用对应的 cases；都不匹配则用 DEFAULT_TEST_CASES。
TEST_CASES_BY_FILE = {
    "个人信息保护法": [
        # —— 列举题：法定处理情形（must_contain 选第 13 条独有的合同条款表述）
        {
            "question": "在哪些情形下个人信息处理者可以处理个人信息而无需单独同意？",
            "must_contain": "为订立、履行个人作为一方当事人的合同所必需",
        },
        # —— 定义题：敏感个人信息的范围
        {
            "question": "敏感个人信息的定义是什么？包含哪些类型的信息？",
            "must_contain": "生物识别、宗教信仰、特定身份、医疗健康",
        },
        # —— 列举题：跨境传输四种合规路径
        {
            "question": "向境外提供个人信息可以通过哪几种合规途径？",
            "must_contain": "经专业机构进行个人信息保护认证",
        },
        # —— 列举题：标准合同条款
        {
            "question": "与境外接收方签订合同时应该约定什么？",
            "must_contain": "按照国家网信部门制定的标准合同",
        },
        # —— 列举题：安全技术措施
        {
            "question": "个人信息处理者应当采取哪些技术性安全措施？",
            "must_contain": "加密、去标识化",
        },
        # —— 数字题：罚款上限（情节严重）
        {
            "question": "处理个人信息违法情节严重的最高罚款金额是多少？",
            "must_contain": "五千万元以下或者上一年度营业额百分之五",
        },
        # —— 流程题：何时需要做影响评估
        {
            "question": "什么情况下需要事前做个人信息保护影响评估？",
            "must_contain": "事前进行个人信息保护影响评估",
        },
        # —— 主体题：国家机关的特殊规定
        {
            "question": "国家机关处理个人信息存储在哪里？需要什么手续才能向境外提供？",
            "must_contain": "在中华人民共和国境内存储",
        },
        # —— 主体题：关键信息基础设施运营者
        {
            "question": "关键信息基础设施运营者向境外传输个人信息有什么要求？",
            "must_contain": "通过国家网信部门组织的安全评估",
        },
        # —— 数字题：未成年人年龄阈值
        {
            "question": "处理多大年龄以下未成年人的个人信息需要监护人同意？",
            "must_contain": "不满十四周岁",
        },
        # —— 权利题：撤回同意
        {"question": "个人能否撤回对个人信息处理的同意？", "must_contain": "便捷的撤回同意的方式"},
        # —— 权利题：可携带权
        {
            "question": "个人能否要求把自己的个人信息转移到指定的处理者？",
            "must_contain": "应当提供转移的途径",
        },
        # —— 权利题：删除权（主动删除情形）
        {
            "question": "在哪些情形下个人信息处理者应当主动删除个人信息？",
            "must_contain": "主动删除个人信息",
        },
        # —— 概念题：自动化决策的约束
        {
            "question": "通过算法做自动化决策时需要遵守什么规则？",
            "must_contain": "不针对其个人特征的选项",
        },
        # —— 概念题：敏感个人信息的同意要求
        {"question": "处理敏感个人信息在同意上有什么特殊要求？", "must_contain": "单独同意"},
    ],
    "数据出境安全申报": [
        {"question": "哪些情形需要申报数据出境安全评估？", "must_contain": "关键信息基础设施"},
        {"question": "数据出境安全评估申报需要提交哪些材料？", "must_contain": "申报书"},
        {"question": "数据出境风险自评估报告应当包含哪些内容？", "must_contain": "风险"},
        {"question": "国家网信办多久会出具评估结果？", "must_contain": "工作日"},
        {"question": "与境外接收方订立的法律文件应当明确什么事项？", "must_contain": "境外接收方"},
        {"question": "重要数据出境的申报要求是什么？", "must_contain": "重要数据"},
        {"question": "申报流程的受理机关是哪一级？", "must_contain": "省级"},
        {"question": "数据处理者如何准备数据出境的自评估？", "must_contain": "自评估"},
    ],
}

# 兜底默认测试集（两个通用问题）
DEFAULT_TEST_CASES = [
    {"question": "数据出境安全评估的适用条件是什么？", "must_contain": "关键信息基础设施"},
    {"question": "标准合同备案需要哪些材料？", "must_contain": "个人信息保护影响评估"},
]


def get_test_cases_for_file(file_path: str) -> list:
    """按文件名关键字选取 test cases。"""
    name = os.path.basename(file_path)
    for key, cases in TEST_CASES_BY_FILE.items():
        if key in name:
            return cases
    return DEFAULT_TEST_CASES


# 兼容旧引用
TEST_CASES = DEFAULT_TEST_CASES

# ── 参数网格 ────────────────────────────────────────────────────────────────
PARAM_GRID = [
    {"chunk_size": 300, "chunk_overlap": 60},
    {"chunk_size": 400, "chunk_overlap": 80},
    {"chunk_size": 600, "chunk_overlap": 120},  # 当前默认值
    {"chunk_size": 800, "chunk_overlap": 160},
    {"chunk_size": 1000, "chunk_overlap": 200},
]


def find_default_file() -> str:
    """从 uploads 目录自动选取第一个可用文件。"""
    if not os.path.isdir(DEFAULT_UPLOAD_DIR):
        raise FileNotFoundError(f"上传目录不存在: {DEFAULT_UPLOAD_DIR}")
    files = [
        f
        for f in os.listdir(DEFAULT_UPLOAD_DIR)
        if os.path.isfile(os.path.join(DEFAULT_UPLOAD_DIR, f))
    ]
    if not files:
        raise FileNotFoundError(f"上传目录为空: {DEFAULT_UPLOAD_DIR}")
    return os.path.join(DEFAULT_UPLOAD_DIR, files[0])


def load_and_clean(file_path: str) -> str:
    """通过项目已有管线加载并清洗文档。"""
    from ingestion.unified_loader import UnifiedLoader
    from processing.cleaner import TextCleaner

    loader = UnifiedLoader()
    doc = loader.load_file(file_path, original_filename=os.path.basename(file_path))
    cleaned = TextCleaner().clean(doc.content)
    return cleaned


def evaluate_split_quality(chunks: list, params: dict) -> dict:
    """切分质量统计。"""
    sizes = [len(c) for c in chunks]
    return {
        "chunk_size": params["chunk_size"],
        "chunk_overlap": params["chunk_overlap"],
        "num_chunks": len(chunks),
        "avg_len": sum(sizes) // len(sizes) if sizes else 0,
        "min_len": min(sizes) if sizes else 0,
        "max_len": max(sizes) if sizes else 0,
        "too_short": sum(1 for s in sizes if s < params["chunk_size"] // 4),
        "too_long": sum(1 for s in sizes if s > params["chunk_size"]),
    }


def evaluate_retrieval_hit(chunks: list, test_cases: list, top_k: int = 5) -> dict:
    """检索命中率：用 embedding 相似度检索，检查答案是否在 top_k 中。"""
    from numpy import dot
    from numpy.linalg import norm

    from retrieval.search.embedder import Embedder

    embedder = Embedder()

    print("  计算 chunk embeddings...")
    chunk_embeddings = embedder.embed_texts(chunks)

    hits = 0
    details = []
    for case in test_cases:
        query_emb = embedder.embed_query(case["question"])

        scores = []
        for i, c_emb in enumerate(chunk_embeddings):
            sim = dot(query_emb, c_emb) / (norm(query_emb) * norm(c_emb) + 1e-9)
            scores.append((float(sim), i))
        scores.sort(reverse=True)
        top_chunks = [chunks[idx] for _, idx in scores[:top_k]]

        found = any(case["must_contain"] in c for c in top_chunks)
        if found:
            hits += 1

        detail = {
            "question": case["question"],
            "hit": found,
            "top1_similarity": scores[0][0],
            "top1_length": len(top_chunks[0]),
        }
        details.append(detail)
        print(f"    Q: {case['question']}")
        print(f"    命中: {'[HIT]' if found else '[MISS]'}  Top1 相似度: {scores[0][0]:.4f}")

    return {
        "hit_rate": hits / len(test_cases) if test_cases else 0,
        "hits": hits,
        "total": len(test_cases),
        "details": details,
    }


def main():
    from processing.splitter import TextSplitter

    parser = argparse.ArgumentParser(description="Chunk 参数评估")
    parser.add_argument("--file", type=str, default=None, help="指定单个测试文档路径")
    parser.add_argument(
        "--files", type=str, nargs="+", default=None, help="指定多个测试文档路径（覆盖 --file）"
    )
    parser.add_argument("--all", action="store_true", help="评测 uploads 目录下所有文件")
    parser.add_argument(
        "--with-retrieval", action="store_true", help="同时评估检索命中率（会调用 embedding API）"
    )
    parser.add_argument("--top-k", type=int, default=5, help="检索时取 top_k（默认 5）")
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="结果 JSON 输出路径，默认写到 evaluations/chunk_params/reports/",
    )
    args = parser.parse_args()

    # 选择待评测文档列表
    if args.all:
        if not os.path.isdir(DEFAULT_UPLOAD_DIR):
            raise FileNotFoundError(f"上传目录不存在: {DEFAULT_UPLOAD_DIR}")
        files_to_eval = [
            os.path.join(DEFAULT_UPLOAD_DIR, f)
            for f in sorted(os.listdir(DEFAULT_UPLOAD_DIR))
            if os.path.isfile(os.path.join(DEFAULT_UPLOAD_DIR, f))
        ]
    elif args.files:
        files_to_eval = args.files
    else:
        files_to_eval = [args.file or find_default_file()]

    all_file_results = []

    for file_path in files_to_eval:
        print(f"\n{'#' * 70}")
        print(f"# 评测文档: {file_path}")
        print(f"{'#' * 70}")

        # 加载 & 清洗
        text = load_and_clean(file_path)
        print(f"清洗后长度: {len(text)} 字符")

        # 选取该文件对应的 test cases
        test_cases = get_test_cases_for_file(file_path)
        print(f"测试问题数: {len(test_cases)}")

        results = []

        for params in PARAM_GRID:
            print(f"\n{'=' * 60}")
            print(f"参数: chunk_size={params['chunk_size']}, overlap={params['chunk_overlap']}")
            print(f"{'=' * 60}")

            splitter = TextSplitter(
                chunk_size=params["chunk_size"],
                chunk_overlap=params["chunk_overlap"],
            )
            t0 = time.time()
            chunks = splitter.split(text)
            split_time = round(time.time() - t0, 3)

            quality = evaluate_split_quality(chunks, params)
            quality["split_time_sec"] = split_time

            print(
                f"  chunk 数: {quality['num_chunks']}  "
                f"avg={quality['avg_len']}  min={quality['min_len']}  max={quality['max_len']}  "
                f"过短={quality['too_short']}  过长={quality['too_long']}  "
                f"耗时={split_time}s"
            )

            if args.with_retrieval and test_cases:
                hit_result = evaluate_retrieval_hit(chunks, test_cases, top_k=args.top_k)
                quality["hit_rate"] = hit_result["hit_rate"]
                quality["retrieval_details"] = hit_result["details"]
                print(
                    f"  命中率: {hit_result['hit_rate']:.0%} ({hit_result['hits']}/{hit_result['total']})"
                )

            results.append(quality)

        # 单文件汇总表
        print(f"\n{'=' * 60}")
        print(f"【{os.path.basename(file_path)}】汇总对比")
        print(f"{'=' * 60}")
        header = (
            f"{'参数':<12} {'chunk数':>7} {'avg':>5} {'min':>5} {'max':>5} {'过短':>4} {'过长':>4}"
        )
        if args.with_retrieval:
            header += f" {'命中率':>7}"
        print(header)
        for r in results:
            row = (
                f"{r['chunk_size']}/{r['chunk_overlap']:<5} "
                f"{r['num_chunks']:>7} {r['avg_len']:>5} {r['min_len']:>5} {r['max_len']:>5} "
                f"{r['too_short']:>4} {r['too_long']:>4}"
            )
            if args.with_retrieval:
                hit = r.get("hit_rate")
                row += f" {hit:>6.0%}" if hit is not None else "      -"
            print(row)

        all_file_results.append(
            {
                "file": file_path,
                "text_length": len(text),
                "test_case_count": len(test_cases),
                "results": results,
            }
        )

    # 保存 JSON（默认归档到 evaluations/chunk_params/reports/）
    if args.output:
        output_path = args.output
    else:
        report_dir = "./evaluations/chunk_params/reports"
        os.makedirs(report_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(report_dir, f"chunk_eval_{ts}.json")
        latest_path = os.path.join(report_dir, "chunk_eval_latest.json")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    report = {
        "timestamp": datetime.now().isoformat(),
        "files_evaluated": len(all_file_results),
        "per_file": all_file_results,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {output_path}")
    if not args.output:
        # 额外写一份固定文件名的 latest，便于 README 引用
        with open(latest_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"latest 快照: {latest_path}")


if __name__ == "__main__":
    main()
