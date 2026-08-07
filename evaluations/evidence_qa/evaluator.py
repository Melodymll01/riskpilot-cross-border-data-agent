"""V3 Evidence QA Claim-Citation 离线评测器。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from domain.qa import (
    ClaimCitationVerifier,
    ClaimSupportJudgement,
    EvidenceQACitation,
    EvidenceQAClaim,
    EvidenceQAScope,
    EvidenceQAStatus,
)

SecurityIssue = Literal[
    "citation_drift",
    "forged_citation",
    "cross_workspace",
    "cross_case",
]
Difficulty = Literal["easy", "medium", "hard"]


class EvaluationModel(BaseModel):
    """评测文件使用严格 schema，避免静默吞掉拼错字段。"""

    model_config = ConfigDict(extra="forbid")


class EvaluationThresholds(EvaluationModel):
    structural_accuracy_min: float = Field(ge=0.0, le=1.0)
    supported_claim_recall_min: float = Field(ge=0.0, le=1.0)
    unsupported_claim_false_accept_rate_max: float = Field(ge=0.0, le=1.0)
    citation_drift_recall_min: float = Field(ge=0.0, le=1.0)
    status_accuracy_min: float = Field(ge=0.0, le=1.0)
    cross_scope_leakage_count_max: int = Field(ge=0)


class GoldClaimSupport(EvaluationModel):
    supported: bool
    supporting_citation_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_support(self) -> GoldClaimSupport:
        if len(self.supporting_citation_ids) != len(set(self.supporting_citation_ids)):
            raise ValueError("supporting_citation_ids 不能重复")
        if self.supported and not self.supporting_citation_ids:
            raise ValueError("受支持 Claim 必须标注 supporting_citation_ids")
        if not self.supported and self.supporting_citation_ids:
            raise ValueError("不受支持 Claim 不能标注 supporting_citation_ids")
        return self


class EvidenceQAGold(EvaluationModel):
    expected_status: EvidenceQAStatus
    expected_structural_valid: bool
    claim_support: dict[str, GoldClaimSupport]
    security_issues: list[SecurityIssue] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_security_issues(self) -> EvidenceQAGold:
        if len(self.security_issues) != len(set(self.security_issues)):
            raise ValueError("security_issues 不能重复")
        return self


class EvidenceQAEvaluationCase(EvaluationModel):
    case_id: str = Field(min_length=1, max_length=100)
    category: str = Field(min_length=1, max_length=100)
    difficulty: Difficulty
    question: str = Field(min_length=1, max_length=2000)
    scope: EvidenceQAScope
    citations: list[EvidenceQACitation] = Field(default_factory=list)
    claims: list[EvidenceQAClaim] = Field(default_factory=list)
    source_integrity_valid: bool = True
    gold: EvidenceQAGold

    @model_validator(mode="after")
    def validate_case(self) -> EvidenceQAEvaluationCase:
        citation_ids = [citation.citation_id for citation in self.citations]
        if len(citation_ids) != len(set(citation_ids)):
            raise ValueError(f"{self.case_id}: citation_id 不能重复")
        claim_ids = [claim.claim_id for claim in self.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError(f"{self.case_id}: claim_id 不能重复")
        if set(self.gold.claim_support) != set(claim_ids):
            raise ValueError(f"{self.case_id}: claim_support 必须覆盖全部 Claim")

        known_citations = set(citation_ids)
        claims_by_id = {claim.claim_id: claim for claim in self.claims}
        for claim_id, support in self.gold.claim_support.items():
            declared = set(claims_by_id[claim_id].citation_ids)
            supporting = set(support.supporting_citation_ids)
            if not supporting.issubset(known_citations):
                raise ValueError(f"{self.case_id}: gold 引用了未知 Citation")
            if not supporting.issubset(declared):
                raise ValueError(f"{self.case_id}: gold 不能扩大 Claim 声明的引用范围")

        structural = ClaimCitationVerifier.verify(self.claims, self.citations)
        if structural.valid != self.gold.expected_structural_valid:
            raise ValueError(f"{self.case_id}: expected_structural_valid 与结构校验结果不一致")

        detected_issues = _detect_case_security_issues(self)
        if detected_issues != set(self.gold.security_issues):
            raise ValueError(f"{self.case_id}: security_issues 与评测输入不一致")

        has_unsupported_claim = any(
            not support.supported for support in self.gold.claim_support.values()
        )
        must_refuse = (
            not self.claims
            or bool(detected_issues)
            or not structural.valid
            or has_unsupported_claim
        )
        if must_refuse and self.gold.expected_status != "refused":
            raise ValueError(f"{self.case_id}: 不安全样本的 expected_status 必须为 refused")
        if not must_refuse and self.gold.expected_status == "refused":
            raise ValueError(f"{self.case_id}: 安全且受支持的样本不应标注为 refused")
        return self


class EvidenceQAEvaluationDataset(EvaluationModel):
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=50)
    description: str = Field(min_length=1)
    created: str = Field(min_length=1, max_length=50)
    usage: str = Field(min_length=1)
    leakage_control: dict[str, object]
    thresholds: EvaluationThresholds
    cases: list[EvidenceQAEvaluationCase] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_cases(self) -> EvidenceQAEvaluationDataset:
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("case_id 不能重复")
        return self


class EvidenceQACasePrediction(EvaluationModel):
    case_id: str = Field(min_length=1, max_length=100)
    status: EvidenceQAStatus
    judgements: list[ClaimSupportJudgement] = Field(default_factory=list)
    detected_security_issues: list[SecurityIssue] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_prediction(self) -> EvidenceQACasePrediction:
        claim_ids = [judgement.claim_id for judgement in self.judgements]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError(f"{self.case_id}: judgement claim_id 不能重复")
        if len(self.detected_security_issues) != len(set(self.detected_security_issues)):
            raise ValueError(f"{self.case_id}: detected_security_issues 不能重复")
        return self


class EvidenceQAPredictions(EvaluationModel):
    dataset_name: str = Field(min_length=1, max_length=200)
    dataset_version: str = Field(min_length=1, max_length=50)
    system: str = Field(min_length=1, max_length=200)
    mode: Literal["production", "oracle_self_check"] = "production"
    cases: list[EvidenceQACasePrediction] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_cases(self) -> EvidenceQAPredictions:
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("预测 case_id 不能重复")
        return self


def load_dataset(path: str | Path) -> EvidenceQAEvaluationDataset:
    return EvidenceQAEvaluationDataset.model_validate(_load_json_object(path))


def load_predictions(path: str | Path) -> EvidenceQAPredictions:
    return EvidenceQAPredictions.model_validate(_load_json_object(path))


def build_oracle_predictions(
    dataset: EvidenceQAEvaluationDataset,
) -> EvidenceQAPredictions:
    """构造标签回放，仅验证评测器协议，不能作为生产模型指标。"""
    predictions: list[EvidenceQACasePrediction] = []
    for case in dataset.cases:
        judgements = []
        for claim in case.claims:
            gold = case.gold.claim_support[claim.claim_id]
            judgements.append(
                ClaimSupportJudgement(
                    claim_id=claim.claim_id,
                    supported=gold.supported,
                    citation_ids=gold.supporting_citation_ids,
                    reason="" if gold.supported else "oracle: gold 标注为不受支持",
                )
            )
        predictions.append(
            EvidenceQACasePrediction(
                case_id=case.case_id,
                status=case.gold.expected_status,
                judgements=judgements,
                detected_security_issues=case.gold.security_issues,
            )
        )
    return EvidenceQAPredictions(
        dataset_name=dataset.name,
        dataset_version=dataset.version,
        system="gold-label-oracle",
        mode="oracle_self_check",
        cases=predictions,
    )


def evaluate(
    dataset: EvidenceQAEvaluationDataset,
    predictions: EvidenceQAPredictions,
) -> dict[str, object]:
    _validate_prediction_contract(dataset, predictions)
    predictions_by_id = {prediction.case_id: prediction for prediction in predictions.cases}

    structural_correct = 0
    supported_total = 0
    supported_accepted = 0
    unsupported_total = 0
    unsupported_accepted = 0
    drift_total = 0
    drift_detected = 0
    status_correct = 0
    cross_scope_leakage_count = 0
    case_results: list[dict[str, object]] = []

    for case in dataset.cases:
        prediction = predictions_by_id[case.case_id]
        structural = ClaimCitationVerifier.verify(case.claims, case.citations)
        structural_match = structural.valid == case.gold.expected_structural_valid
        structural_correct += int(structural_match)

        judgements_by_id = {judgement.claim_id: judgement for judgement in prediction.judgements}
        semantic_matches: dict[str, bool] = {}
        for claim in case.claims:
            gold = case.gold.claim_support[claim.claim_id]
            predicted_supported = judgements_by_id[claim.claim_id].supported
            semantic_matches[claim.claim_id] = predicted_supported == gold.supported
            if gold.supported:
                supported_total += 1
                supported_accepted += int(predicted_supported)
            else:
                unsupported_total += 1
                unsupported_accepted += int(predicted_supported)

        has_drift = "citation_drift" in case.gold.security_issues
        if has_drift:
            drift_total += 1
            drift_detected += int("citation_drift" in prediction.detected_security_issues)

        status_match = prediction.status == case.gold.expected_status
        status_correct += int(status_match)
        cross_scope_issue = bool({"cross_workspace", "cross_case"} & set(case.gold.security_issues))
        leaked = cross_scope_issue and prediction.status != "refused"
        cross_scope_leakage_count += int(leaked)
        case_results.append(
            {
                "case_id": case.case_id,
                "category": case.category,
                "difficulty": case.difficulty,
                "expected_status": case.gold.expected_status,
                "predicted_status": prediction.status,
                "structural_valid": structural.valid,
                "structural_match": structural_match,
                "semantic_matches": semantic_matches,
                "expected_security_issues": case.gold.security_issues,
                "detected_security_issues": prediction.detected_security_issues,
                "cross_scope_leaked": leaked,
                "passed": (
                    structural_match
                    and all(semantic_matches.values())
                    and status_match
                    and not leaked
                    and (not has_drift or "citation_drift" in prediction.detected_security_issues)
                ),
            }
        )

    case_count = len(dataset.cases)
    metrics: dict[str, int | float] = {
        "case_count": case_count,
        "claim_count": supported_total + unsupported_total,
        "structural_accuracy": _safe_ratio(structural_correct, case_count, empty=1.0),
        "supported_claim_recall": _safe_ratio(
            supported_accepted,
            supported_total,
            empty=1.0,
        ),
        "unsupported_claim_false_accept_rate": _safe_ratio(
            unsupported_accepted,
            unsupported_total,
            empty=0.0,
        ),
        "citation_drift_recall": _safe_ratio(
            drift_detected,
            drift_total,
            empty=1.0,
        ),
        "status_accuracy": _safe_ratio(status_correct, case_count, empty=1.0),
        "cross_scope_leakage_count": cross_scope_leakage_count,
    }
    gates = _evaluate_gates(metrics, dataset.thresholds)
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": {
            "name": dataset.name,
            "version": dataset.version,
            "case_count": case_count,
        },
        "candidate": {
            "system": predictions.system,
            "mode": predictions.mode,
            "production_evidence": predictions.mode == "production",
        },
        "metrics": metrics,
        "thresholds": dataset.thresholds.model_dump(),
        "gates": gates,
        "passed": all(gate["passed"] for gate in gates.values()),
        "cases": case_results,
    }


def write_report(
    report: dict[str, object],
    output_dir: str | Path,
) -> tuple[Path, Path, Path]:
    report_dir = Path(output_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archived_json = report_dir / f"evidence_qa_eval_{timestamp}.json"
    latest_json = report_dir / "evidence_qa_eval_latest.json"
    latest_markdown = report_dir / "evidence_qa_eval_latest.md"
    body = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    archived_json.write_text(body, encoding="utf-8")
    latest_json.write_text(body, encoding="utf-8")
    latest_markdown.write_text(_render_markdown(report), encoding="utf-8")
    return archived_json, latest_json, latest_markdown


def _validate_prediction_contract(
    dataset: EvidenceQAEvaluationDataset,
    predictions: EvidenceQAPredictions,
) -> None:
    if predictions.dataset_name != dataset.name:
        raise ValueError("预测文件 dataset_name 与评测集不一致")
    if predictions.dataset_version != dataset.version:
        raise ValueError("预测文件 dataset_version 与评测集不一致")
    expected_case_ids = {case.case_id for case in dataset.cases}
    actual_case_ids = {case.case_id for case in predictions.cases}
    if actual_case_ids != expected_case_ids:
        missing = sorted(expected_case_ids - actual_case_ids)
        unknown = sorted(actual_case_ids - expected_case_ids)
        raise ValueError(f"预测 case 覆盖不完整: missing={missing}, unknown={unknown}")

    cases_by_id = {case.case_id: case for case in dataset.cases}
    for prediction in predictions.cases:
        case = cases_by_id[prediction.case_id]
        expected_claim_ids = {claim.claim_id for claim in case.claims}
        actual_claim_ids = {judgement.claim_id for judgement in prediction.judgements}
        if actual_claim_ids != expected_claim_ids:
            raise ValueError(f"{case.case_id}: judgements 必须且只能覆盖全部 Claim")
        known_citations = {citation.citation_id for citation in case.citations}
        claims_by_id = {claim.claim_id: claim for claim in case.claims}
        for judgement in prediction.judgements:
            used = set(judgement.citation_ids)
            declared = set(claims_by_id[judgement.claim_id].citation_ids)
            if not used.issubset(known_citations):
                raise ValueError(f"{case.case_id}: judgement 引用了未知 Citation")
            if judgement.supported and not used.issubset(declared):
                raise ValueError(f"{case.case_id}: judgement 扩大了 Claim 引用范围")


def _detect_case_security_issues(case: EvidenceQAEvaluationCase) -> set[SecurityIssue]:
    issues: set[SecurityIssue] = set()
    known_citations = {citation.citation_id for citation in case.citations}
    referenced_citations = {
        citation_id for claim in case.claims for citation_id in claim.citation_ids
    }
    if referenced_citations - known_citations:
        issues.add("forged_citation")
    if not case.source_integrity_valid:
        issues.add("citation_drift")
    for citation in case.citations:
        if (
            case.scope.workspace_id is not None
            and citation.workspace_id is not None
            and citation.workspace_id != case.scope.workspace_id
        ):
            issues.add("cross_workspace")
        if (
            case.scope.case_id is not None
            and citation.case_id is not None
            and citation.case_id != case.scope.case_id
        ):
            issues.add("cross_case")
    return issues


def _evaluate_gates(
    metrics: dict[str, int | float],
    thresholds: EvaluationThresholds,
) -> dict[str, dict[str, object]]:
    definitions = {
        "structural_accuracy": (
            metrics["structural_accuracy"],
            ">=",
            thresholds.structural_accuracy_min,
        ),
        "supported_claim_recall": (
            metrics["supported_claim_recall"],
            ">=",
            thresholds.supported_claim_recall_min,
        ),
        "unsupported_claim_false_accept_rate": (
            metrics["unsupported_claim_false_accept_rate"],
            "<=",
            thresholds.unsupported_claim_false_accept_rate_max,
        ),
        "citation_drift_recall": (
            metrics["citation_drift_recall"],
            ">=",
            thresholds.citation_drift_recall_min,
        ),
        "status_accuracy": (
            metrics["status_accuracy"],
            ">=",
            thresholds.status_accuracy_min,
        ),
        "cross_scope_leakage_count": (
            metrics["cross_scope_leakage_count"],
            "<=",
            thresholds.cross_scope_leakage_count_max,
        ),
    }
    gates: dict[str, dict[str, object]] = {}
    for name, (actual, operator, threshold) in definitions.items():
        passed = actual >= threshold if operator == ">=" else actual <= threshold
        gates[name] = {
            "actual": actual,
            "operator": operator,
            "threshold": threshold,
            "passed": passed,
        }
    return gates


def _render_markdown(report: dict[str, object]) -> str:
    dataset = report["dataset"]
    candidate = report["candidate"]
    metrics = report["metrics"]
    gates = report["gates"]
    assert isinstance(dataset, dict)
    assert isinstance(candidate, dict)
    assert isinstance(metrics, dict)
    assert isinstance(gates, dict)
    lines = [
        "# V3 Evidence QA 评测报告",
        "",
        f"- 数据集：`{dataset['name']}@{dataset['version']}`",
        f"- 候选系统：`{candidate['system']}`",
        f"- 运行模式：`{candidate['mode']}`",
        f"- 是否构成生产效果证据：`{str(candidate['production_evidence']).lower()}`",
        f"- 总门禁：**{'PASS' if report['passed'] else 'FAIL'}**",
        "",
        "## 指标",
        "",
        "| 指标 | 结果 |",
        "| --- | ---: |",
    ]
    for name, value in metrics.items():
        rendered = f"{value:.2%}" if isinstance(value, float) else str(value)
        lines.append(f"| `{name}` | {rendered} |")
    lines.extend(
        [
            "",
            "## 门禁",
            "",
            "| 门禁 | 实际值 | 条件 | 阈值 | 结果 |",
            "| --- | ---: | :---: | ---: | :---: |",
        ]
    )
    for name, raw_gate in gates.items():
        assert isinstance(raw_gate, dict)
        actual = raw_gate["actual"]
        threshold = raw_gate["threshold"]
        actual_text = f"{actual:.4f}" if isinstance(actual, float) else str(actual)
        threshold_text = f"{threshold:.4f}" if isinstance(threshold, float) else str(threshold)
        lines.append(
            f"| `{name}` | {actual_text} | {raw_gate['operator']} | "
            f"{threshold_text} | {'PASS' if raw_gate['passed'] else 'FAIL'} |"
        )
    if candidate["mode"] == "oracle_self_check":
        lines.extend(
            [
                "",
                "> 当前为 gold-label oracle 自检，只证明数据集、指标和门禁实现一致，"
                "不能作为生产模型效果或安全性证据。",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def _load_json_object(path: str | Path) -> dict[str, object]:
    file_path = Path(path)
    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{file_path} 不是合法 JSON") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{file_path} 顶层必须是 JSON 对象")
    return raw


def _safe_ratio(numerator: int, denominator: int, *, empty: float) -> float:
    return empty if denominator == 0 else numerator / denominator
