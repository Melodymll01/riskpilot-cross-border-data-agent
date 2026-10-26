"""Structured risk analysis whose factual findings share the Evidence QA verifier."""

from __future__ import annotations

import json
import time
import uuid
from contextlib import nullcontext
from typing import Literal

from app.use_cases.evidence_qa import EvidenceQAUseCase
from domain.ports import ChatPort, RunControlPort, TracePort
from domain.qa import (
    EvidenceQACitation,
    EvidenceQAClaim,
    EvidenceQADraft,
    EvidenceQAScope,
    UserContextMessage,
)
from domain.risk import RiskAssessment, RiskAssessmentRepoPort, RiskDraft, RiskFinding

_SYSTEM = """你是跨境数据合规的初步风险分析器。
仅基于提供的真实证据与本轮用户条件分析，不使用账号记忆作为已确认事实。
输出 JSON，包含 findings 和 missing_information。
findings 中每项包含 finding_id、text、severity(low/medium/high)、citation_ids、
condition_message_ids、recommendation。所有事实与建议都必须有对应证据支持。
用户条件引用真实 message_id；法规引用 SRC-*。不得发明来源、人数、法域或日期。
severity 是建议关注程度，不是法律机关的正式风险评级。不得宣称已经合规或自动通过审批。
缺少关键事实或适用法规时在 missing_information 中列出；不确定不等于低风险。
涉及法律人数/日期门槛而缺少可核对条件时应要求补充，不凭模型猜测法律路径。
保留法规的条件与例外。例外是否适用尚不明确时，描述“待核实风险”，不得直接断言违法。
recommendation 可以为空；没有直接依据时不要编造固定期限或其他法律义务。
最多8条风险项，只返回 JSON，不输出思维链。"""


class RiskAssessmentUseCase:
    def __init__(
        self,
        chat: ChatPort,
        evidence_qa: EvidenceQAUseCase,
        repo: RiskAssessmentRepoPort,
        trace: TracePort | None = None,
    ) -> None:
        self._chat, self._qa, self._repo = chat, evidence_qa, repo
        self._trace = trace

    def assess(
        self,
        *,
        owner_id: str,
        run_id: str,
        question: str,
        scope: EvidenceQAScope,
        citations: list[EvidenceQACitation],
        user_context: list[UserContextMessage],
        runtime_control: RunControlPort,
    ) -> RiskAssessment:
        context = (
            self._trace.span(
                "riskpilot.risk.assess",
                metadata={
                    "owner_id": owner_id,
                    "run_id": run_id,
                    "evidence_count": len(citations),
                },
            )
            if self._trace
            else nullcontext(None)
        )
        with context as span:
            try:
                result = self._assess(
                    owner_id=owner_id,
                    run_id=run_id,
                    question=question,
                    scope=scope,
                    citations=citations,
                    user_context=user_context,
                    runtime_control=runtime_control,
                )
            except Exception as exc:
                if span is not None:
                    span.add_metadata({"status": "failed", "error_type": type(exc).__name__})
                raise
            if span is not None:
                span.add_metadata(
                    {
                        "status": "completed",
                        "decision": result.status,
                        "evidence_count": len(result.citations),
                    }
                )
            return result

    def _assess(
        self,
        *,
        owner_id: str,
        run_id: str,
        question: str,
        scope: EvidenceQAScope,
        citations: list[EvidenceQACitation],
        user_context: list[UserContextMessage],
        runtime_control: RunControlPort,
    ) -> RiskAssessment:
        runtime_control.check()
        if scope.owner_id != owner_id:
            raise ValueError("风险评估范围与当前用户不一致")
        sources = self._qa.verify_sources(citations, scope)
        if not sources:
            return self._store(
                owner_id,
                run_id,
                question,
                status="needs_information",
                missing=["没有可验证证据，请选择材料并检索适用法规。"],
            )
        raw = self._chat.chat(
            [
                {"role": "system", "content": _SYSTEM},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "question": question,
                            "evidence": [item.model_dump() for item in sources],
                            "user_conditions": [item.model_dump() for item in user_context],
                            "required_json_schema": RiskDraft.model_json_schema(),
                            "output_example": {
                                "findings": [
                                    {
                                        "finding_id": "R1",
                                        "text": "风险说明",
                                        "severity": "medium",
                                        "citation_ids": ["实际证据ID"],
                                        "condition_message_ids": [],
                                        "recommendation": "有依据的建议",
                                    }
                                ],
                                "missing_information": [],
                            },
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            json_mode=True,
            temperature=0,
            runtime_control=runtime_control,
            operation="risk_analysis",
        )
        draft = RiskDraft.model_validate_json(raw)
        runtime_control.check()
        claims = []
        for item in draft.findings:
            claims.append(
                EvidenceQAClaim(
                    claim_id=item.finding_id,
                    text=item.text,
                    citation_ids=item.citation_ids,
                    condition_message_ids=item.condition_message_ids,
                )
            )
            if item.recommendation:
                claims.append(
                    EvidenceQAClaim(
                        claim_id=item.finding_id + ":recommendation",
                        text=item.recommendation,
                        citation_ids=item.citation_ids,
                        condition_message_ids=item.condition_message_ids,
                    )
                )
        verified = self._qa.verify_draft(
            question=question,
            scope=scope,
            citations=sources,
            draft=EvidenceQADraft(
                status="answered" if claims else "refused",
                claims=claims,
                refusal_reason="未形成有依据的风险项" if not claims else "",
            ),
            user_context=user_context,
            runtime_control=runtime_control,
        )
        kept = {item.claim_id for item in verified.claims}
        findings = [
            item.model_copy(
                update={
                    "recommendation": item.recommendation
                    if item.finding_id + ":recommendation" in kept
                    else ""
                }
            )
            for item in draft.findings
            if item.finding_id in kept
        ]
        missing: list[str] = list(draft.missing_information)
        if not any(item.corpus == "regulatory" for item in sources):
            missing.append("尚缺适用法规原文，不能形成完整合规风险判断。")
        if not any(item.corpus in {"user_docs", "knowledge"} for item in sources):
            missing.append("尚缺本人的具体材料证据，请选择材料后评估。")
        if len(verified.claims) < len(claims):
            missing.append("部分风险项或建议未通过证据校验，需要补充依据。")
        status: Literal["assessed", "needs_information", "refused"] = (
            "assessed" if findings and not missing else "needs_information"
        )
        if not findings and not missing:
            status = "refused"
            missing.append("现有证据不支持风险结论。")
        runtime_control.check()
        return self._store(
            owner_id,
            run_id,
            question,
            status=status,
            missing=missing,
            findings=findings,
            citations=verified.citations,
            input_citation_ids=[item.citation_id for item in sources],
        )

    def _store(
        self,
        owner_id: str,
        run_id: str,
        question: str,
        *,
        status: Literal["assessed", "needs_information", "refused"],
        missing: list[str],
        findings: list[RiskFinding] | None = None,
        citations: list[EvidenceQACitation] | None = None,
        input_citation_ids: list[str] | None = None,
    ) -> RiskAssessment:
        findings = findings or []
        levels = {"low": 1, "medium": 2, "high": 3}
        risk_level = (
            max((item.severity for item in findings), key=levels.__getitem__)
            if status == "assessed"
            else "unknown"
        )
        result = RiskAssessment(
            assessment_id="risk_" + uuid.uuid4().hex,
            owner_id=owner_id,
            run_id=run_id,
            question=question,
            status=status,
            risk_level=risk_level,
            findings=findings,
            missing_information=list(dict.fromkeys(missing)),
            citations=citations or [],
            input_citation_ids=input_citation_ids or [],
            created_at=time.time(),
        )
        self._repo.save(result)
        return result

    def for_run(self, owner_id: str, run_id: str) -> RiskAssessment | None:
        return self._repo.for_run(owner_id, run_id)

    def revalidate(self, result: RiskAssessment, scope: EvidenceQAScope) -> None:
        sources = self._qa.verify_sources(result.citations, scope)
        if {item.citation_id for item in sources} != {
            item.citation_id for item in result.citations
        }:
            raise ValueError("风险评估证据已失效，请重新评估")


def render_risk_assessment(result: RiskAssessment) -> str:
    levels = {"unknown": "信息不足，暂不评级", "low": "低", "medium": "中", "high": "高"}
    lines = [
        "**初步风险分析**",
        f"关注程度：{levels[result.risk_level]}（基于当前证据，需人工复核）",
    ]
    for item in result.findings:
        refs = " ".join(f"[{cid}]" for cid in item.citation_ids)
        lines.append(f"- {item.text} {refs}")
        if item.recommendation:
            lines.append(f"  建议：{item.recommendation}")
    if result.missing_information:
        lines.append("\n需要补充：")
        lines.extend("- " + item for item in result.missing_information)
    return "\n".join(lines)
