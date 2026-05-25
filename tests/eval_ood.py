"""OOD 分类器 + 细分类型评测脚本（适配 v2 数据集）。

v2 相对 v1 的升级：
- 评测集切换到 eval_dataset_v2.json（长文本 + 复杂业务场景）
- 支持 secondary_types 软标签：主类型命中计 1.0，次类型命中计 0.5
- 支持按 difficulty（easy/medium/hard）分档统计
- 新增 in-domain 细分类型准确率（5 类各自的命中情况）

设计原则：
- 评测集单一来源（single source of truth）
- 数据缺失/损坏时 fail loud
- 报告归档：evaluations/ood/reports/ 下生成带时间戳的 txt/json 和固定文件名的 latest.md

运行：python -m tests.eval_ood
"""

import sys
import os
import json
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from retrieval.agent.question_classifier import QuestionClassifier


# 评测集：切换到 v2
_DATASET_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "evaluations", "ood", "datasets", "eval_dataset_v2.json",
)

# 软标签计分权重
PRIMARY_HIT_SCORE = 1.0
SECONDARY_HIT_SCORE = 0.5


def load_eval_cases():
    """加载 v2 评测集。缺失/损坏立即 fail loud。"""
    if not os.path.exists(_DATASET_PATH):
        raise FileNotFoundError(
            f"评测集文件不存在: {_DATASET_PATH}\n"
            f"请确认 evaluations/ood/datasets/eval_dataset_v2.json 存在。"
        )
    with open(_DATASET_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    cases = data.get("cases")
    if not cases or not isinstance(cases, list):
        raise ValueError(f"评测集 'cases' 字段为空或格式错误: {_DATASET_PATH}")
    return data, cases


DATASET_META, EVAL_CASES = load_eval_cases()


def main():
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    report_dir = os.path.join(project_root, "evaluations", "ood", "reports")
    os.makedirs(report_dir, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    txt_path = os.path.join(report_dir, f"ood_eval_{ts}.txt")
    md_path = os.path.join(report_dir, "ood_eval_latest.md")
    json_path = os.path.join(report_dir, f"ood_eval_{ts}.json")

    lines = []

    def emit(s: str = ""):
        print(s)
        lines.append(s)

    classifier = QuestionClassifier()

    # ========== 计数器 ==========
    tp_ood = 0
    fn_ood = 0
    tp_in = 0
    fn_in = 0

    hard_count = 0
    probe_count = 0
    down_count = 0

    fine_primary_hit = 0
    fine_secondary_hit = 0
    fine_miss = 0
    fine_score_sum = 0.0
    fine_total = 0

    diff_stats = defaultdict(lambda: [0.0, 0])
    type_stats = defaultdict(lambda: [0, 0])

    errors = []
    details = []

    emit(f"{'='*80}")
    emit(f"OOD + 细分类型评测报告（数据集 {DATASET_META.get('name')} v{DATASET_META.get('version')}）")
    emit(f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    emit(f"样本数：{len(EVAL_CASES)}")
    emit(f"{'='*80}\n")

    for i, case in enumerate(EVAL_CASES, 1):
        query = case["query"]
        label = case["label"]
        expected_type = case.get("expected_type")
        secondary_types = case.get("secondary_types") or []
        difficulty = case.get("difficulty", "unknown")

        try:
            analysis = classifier.classify(query)
        except Exception as e:
            emit(f"[{i}] {query[:40]}... → 异常: {e}")
            continue

        predicted_type = analysis.question_type
        predicted_label = "ood" if analysis.is_out_of_scope else "in_domain"
        ood_correct = (predicted_label == label)
        decision = analysis.ood_decision if analysis.is_out_of_scope else "-"

        # ---- OOD 二分类 ----
        if label == "ood":
            if ood_correct:
                tp_ood += 1
                if decision == "hard_refuse":
                    hard_count += 1
                elif decision == "probe":
                    probe_count += 1
                elif decision == "downgrade":
                    down_count += 1
            else:
                fn_ood += 1
                errors.append({
                    "type": "OOD 漏判",
                    "query": query,
                    "expected": label,
                    "predicted": predicted_type,
                    "confidence": analysis.confidence,
                    "difficulty": difficulty,
                })
        else:
            if ood_correct:
                tp_in += 1
            else:
                fn_in += 1
                errors.append({
                    "type": "in-domain 误杀",
                    "query": query,
                    "expected": expected_type,
                    "predicted": predicted_type,
                    "confidence": analysis.confidence,
                    "difficulty": difficulty,
                })

        # ---- 细分类型（仅在 in_domain 且 OOD 正确时统计） ----
        fine_hit_kind = None
        fine_score = 0.0
        if label == "in_domain" and ood_correct:
            fine_total += 1
            type_stats[expected_type][1] += 1
            if predicted_type == expected_type:
                fine_primary_hit += 1
                fine_hit_kind = "primary"
                fine_score = PRIMARY_HIT_SCORE
                type_stats[expected_type][0] += 1
            elif predicted_type in secondary_types:
                fine_secondary_hit += 1
                fine_hit_kind = "secondary"
                fine_score = SECONDARY_HIT_SCORE
            else:
                fine_miss += 1
                fine_hit_kind = "miss"
                fine_score = 0.0
                errors.append({
                    "type": "细分类型错判",
                    "query": query,
                    "expected": expected_type,
                    "secondary": secondary_types,
                    "predicted": predicted_type,
                    "confidence": analysis.confidence,
                    "difficulty": difficulty,
                })
            fine_score_sum += fine_score
            diff_stats[difficulty][0] += fine_score
            diff_stats[difficulty][1] += 1

        mark = "✓" if ood_correct else "✗"
        if fine_hit_kind == "primary":
            fine_mark = " [主命中]"
        elif fine_hit_kind == "secondary":
            fine_mark = " [次命中 0.5]"
        elif fine_hit_kind == "miss":
            fine_mark = " [细分错]"
        else:
            fine_mark = ""

        emit(
            f"[{i:2d}] {mark} {query[:32]:<32} "
            f"label={label:<10} exp={str(expected_type):<11} "
            f"pred={predicted_type:<13} conf={analysis.confidence:.2f} "
            f"diff={difficulty}{fine_mark}"
            + (f" decision={decision}" if analysis.is_out_of_scope else "")
        )

        details.append({
            "idx": i,
            "query": query,
            "label": label,
            "expected_type": expected_type,
            "secondary_types": secondary_types,
            "difficulty": difficulty,
            "predicted_type": predicted_type,
            "predicted_label": analysis.type_label,
            "confidence": round(analysis.confidence, 3),
            "ood_correct": ood_correct,
            "fine_hit_kind": fine_hit_kind,
            "fine_score": fine_score,
            "decision": decision,
            "reasoning": analysis.reasoning,
        })

    # ========== 汇总 ==========
    total_ood = tp_ood + fn_ood
    total_in = tp_in + fn_in

    ood_recall = tp_ood / total_ood if total_ood else 0
    in_accuracy = tp_in / total_in if total_in else 0
    kill_rate = fn_in / total_in if total_in else 0

    fine_strict_acc = fine_primary_hit / fine_total if fine_total else 0
    fine_soft_acc = fine_score_sum / fine_total if fine_total else 0

    emit(f"\n{'='*80}")
    emit("【一、OOD 二分类指标】")
    emit(f"{'='*80}")
    emit(f"  总样本:                {len(EVAL_CASES)}")
    emit(f"  in-domain 样本:        {total_in}")
    emit(f"  OOD 样本:              {total_ood}")
    emit(f"  in-domain 误杀率:      {kill_rate:.1%}  (理想 <5%)")
    emit(f"  OOD 召回率:            {ood_recall:.1%}  (理想 >85%)")
    emit(f"  in-domain 放行率:      {in_accuracy:.1%}")
    emit(f"\n  OOD 软判决分布（{tp_ood} 条真 OOD 正确识别）:")
    emit(f"    hard_refuse : {hard_count}")
    emit(f"    probe       : {probe_count}")
    emit(f"    downgrade   : {down_count}")

    emit(f"\n{'='*80}")
    emit("【二、in-domain 细分类型指标】（definition/comparison/condition/process/case）")
    emit(f"{'='*80}")
    emit(f"  参评样本:              {fine_total}（仅统计 OOD 放行正确的 in-domain）")
    emit(f"  严格准确率（仅主命中）: {fine_strict_acc:.1%}")
    emit(f"  软标签准确率:          {fine_soft_acc:.1%}  (主=1.0, 次={SECONDARY_HIT_SCORE})")
    emit(f"  主命中 / 次命中 / 错判: {fine_primary_hit} / {fine_secondary_hit} / {fine_miss}")
    emit(f"\n  各主类型严格命中情况:")
    for t in ["definition", "comparison", "condition", "process", "case"]:
        hit, total = type_stats[t]
        rate = hit / total if total else 0
        emit(f"    {t:<12}: {hit}/{total}  ({rate:.1%})")

    emit(f"\n{'='*80}")
    emit("【三、按难度分档的软标签准确率】")
    emit(f"{'='*80}")
    for diff in ["easy", "medium", "hard"]:
        s, t = diff_stats[diff]
        rate = s / t if t else 0
        emit(f"  {diff:<8}: {rate:.1%}  (得分 {s:.1f}/{t})")

    emit(f"\n{'='*80}")
    emit(f"【四、Bad Cases（共 {len(errors)} 条）】")
    emit(f"{'='*80}")
    if errors:
        for e in errors:
            if e["type"] == "细分类型错判":
                emit(
                    f"  [{e['type']}] diff={e['difficulty']} conf={e['confidence']:.2f} "
                    f"exp={e['expected']} sec={e.get('secondary')} pred={e['predicted']}  {e['query'][:50]}"
                )
            else:
                emit(
                    f"  [{e['type']}] diff={e['difficulty']} conf={e['confidence']:.2f} "
                    f"exp={e['expected']} pred={e['predicted']}  {e['query'][:50]}"
                )
    else:
        emit("  无 bad case")

    emit(f"\n{'='*80}")
    emit("【五、面试话术】")
    emit(f"{'='*80}")
    emit(f"  在 v2 评测集 {len(EVAL_CASES)} 条（长文本+复杂场景）上：")
    emit(f"  - in-domain 误杀率 {kill_rate:.1%}、OOD 召回率 {ood_recall:.1%}")
    emit(f"  - 5 类细分：严格 {fine_strict_acc:.1%}、软标签 {fine_soft_acc:.1%}")
    emit(f"  - 难度分档揭示模型在 hard 档的退化幅度，用于指导 prompt 调优方向")

    # ========== 写文件 ==========
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    json_data = {
        "timestamp": datetime.now().isoformat(),
        "dataset": {
            "name": DATASET_META.get("name"),
            "version": DATASET_META.get("version"),
            "total_samples": len(EVAL_CASES),
        },
        "ood_metrics": {
            "in_domain_kill_rate": round(kill_rate, 4),
            "ood_recall": round(ood_recall, 4),
            "in_domain_accuracy": round(in_accuracy, 4),
        },
        "fine_grained_metrics": {
            "strict_accuracy": round(fine_strict_acc, 4),
            "soft_accuracy": round(fine_soft_acc, 4),
            "primary_hit": fine_primary_hit,
            "secondary_hit": fine_secondary_hit,
            "miss": fine_miss,
            "total": fine_total,
        },
        "per_type_accuracy": {
            t: {
                "hit": type_stats[t][0],
                "total": type_stats[t][1],
                "accuracy": round(type_stats[t][0] / type_stats[t][1], 4) if type_stats[t][1] else 0,
            }
            for t in ["definition", "comparison", "condition", "process", "case"]
        },
        "difficulty_breakdown": {
            diff: {
                "soft_accuracy": round(diff_stats[diff][0] / diff_stats[diff][1], 4) if diff_stats[diff][1] else 0,
                "score_sum": round(diff_stats[diff][0], 2),
                "total": diff_stats[diff][1],
            }
            for diff in ["easy", "medium", "hard"]
        },
        "counts": {
            "in_domain_total": total_in,
            "ood_total": total_ood,
            "ood_correctly_detected": tp_ood,
            "ood_missed": fn_ood,
            "in_domain_killed": fn_in,
            "decision_hard_refuse": hard_count,
            "decision_probe": probe_count,
            "decision_downgrade": down_count,
        },
        "bad_cases": errors,
        "details": details,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)

    # Markdown 报告
    md_lines = [
        f"# OOD + 细分类型评测报告",
        "",
        f"**评测时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**数据集**: {DATASET_META.get('name')} (v{DATASET_META.get('version')})",
        f"**样本规模**: {len(EVAL_CASES)} 条（in-domain {total_in} + OOD {total_ood}）",
        "",
        "## 一、OOD 二分类",
        "",
        "| 指标 | 数值 | 目标 |",
        "| --- | --- | --- |",
        f"| in-domain 误杀率 | **{kill_rate:.1%}** | <5% |",
        f"| OOD 召回率 | **{ood_recall:.1%}** | >85% |",
        f"| in-domain 放行率 | {in_accuracy:.1%} | >95% |",
        "",
        "### OOD 软判决分布",
        f"- `hard_refuse`: {hard_count}",
        f"- `probe`: {probe_count}",
        f"- `downgrade`: {down_count}",
        "",
        "## 二、in-domain 细分类型（软标签）",
        "",
        "| 指标 | 数值 |",
        "| --- | --- |",
        f"| 严格准确率（仅主命中） | **{fine_strict_acc:.1%}** |",
        f"| 软标签准确率（主=1.0, 次=0.5） | **{fine_soft_acc:.1%}** |",
        f"| 主命中 / 次命中 / 错判 | {fine_primary_hit} / {fine_secondary_hit} / {fine_miss} |",
        "",
        "### 各主类型严格命中率",
        "",
        "| 类型 | 命中/总数 | 准确率 |",
        "| --- | --- | --- |",
    ]
    for t in ["definition", "comparison", "condition", "process", "case"]:
        hit, total = type_stats[t]
        rate = hit / total if total else 0
        md_lines.append(f"| {t} | {hit}/{total} | {rate:.1%} |")

    md_lines += [
        "",
        "## 三、按难度分档（软标签准确率）",
        "",
        "| 难度 | 准确率 | 得分/总数 |",
        "| --- | --- | --- |",
    ]
    for diff in ["easy", "medium", "hard"]:
        s, t = diff_stats[diff]
        rate = s / t if t else 0
        md_lines.append(f"| {diff} | {rate:.1%} | {s:.1f}/{t} |")

    md_lines += ["", "## 四、Bad Cases", ""]
    if errors:
        md_lines.append("| 错误类型 | 难度 | 置信度 | 期望 | 预测 | Query |")
        md_lines.append("| --- | --- | --- | --- | --- | --- |")
        for e in errors:
            md_lines.append(
                f"| {e['type']} | {e['difficulty']} | {e['confidence']:.2f} | "
                f"{e.get('expected')} | {e['predicted']} | {e['query'][:50]} |"
            )
    else:
        md_lines.append("_无 bad case_")

    md_lines += [
        "",
        "## 五、结论",
        "",
        f"- OOD 二分类：误杀率 {kill_rate:.1%}、召回率 {ood_recall:.1%}",
        f"- 细分类型（5 类）：严格 {fine_strict_acc:.1%} → 软标签 {fine_soft_acc:.1%}",
        f"  软标签上浮说明 {fine_secondary_hit} 条样本命中 secondary_types，验证跨类型软标签设计的合理性。",
        "- 按难度分档的差距揭示模型瓶颈，可针对 hard 案例补充 few-shot 或拆解 prompt。",
    ]
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))

    emit("")
    emit(f"{'='*80}")
    emit("报告已保存：")
    emit(f"  - 文本:     {txt_path}")
    emit(f"  - JSON:     {json_path}")
    emit(f"  - Markdown: {md_path}")
    emit(f"{'='*80}")


if __name__ == "__main__":
    main()
