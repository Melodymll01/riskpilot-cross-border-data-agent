"""基于 ChatPort 的结构化 Evidence QA 生成与独立支持校验。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from domain.qa import (
    ClaimSupportJudgement,
    ClaimSupportResult,
    EvidenceQACitation,
    EvidenceQAClaim,
    EvidenceQADraft,
)

if TYPE_CHECKING:
    from domain.ports import ChatPort

_GENERATOR_SYSTEM = """你是 Evidence QA 的结构化 Claim 生成器。

你只能使用用户消息中编号为 E1、E2... 的证据原文，不得使用训练知识。
把答案拆成可独立核验的原子 Claim。每个 Claim 必须引用至少一个证据 ID。
证据不足时返回 refused；只能回答部分时返回 partially_answered 并列出 unanswered_aspects。
不要输出思维链、Markdown 或 JSON 之外的文本。

输出 JSON：
{
  "status": "answered" | "partially_answered" | "refused",
  "claims": [
    {"claim_id": "C1", "text": "...", "citation_ids": ["E1"]}
  ],
  "refusal_reason": "",
  "unanswered_aspects": []
}
"""

_VERIFIER_SYSTEM = """你是独立的 Claim-Citation 语义支持校验器。

逐个判断 Claim 是否能由它声明引用的证据原文直接支持。不要因为 Claim 看起来合理就通过，
不要使用训练知识，不要补充推理链。只返回 JSON。

输出 JSON：
{
  "judgements": [
    {
      "claim_id": "C1",
      "supported": true,
      "citation_ids": ["E1"],
      "reason": ""
    }
  ]
}
"""


class StructuredEvidenceQAGenerator:
    def __init__(self, chat: ChatPort) -> None:
        self._chat = chat

    def generate(
        self,
        *,
        question: str,
        citations: list[EvidenceQACitation],
    ) -> EvidenceQADraft:
        if not question.strip():
            raise ValueError("question 不能为空")
        if not citations:
            return EvidenceQADraft(
                status="refused",
                refusal_reason="根据当前检索范围未找到足够证据。",
            )
        raw = self._chat.chat(
            [
                {"role": "system", "content": _GENERATOR_SYSTEM},
                {
                    "role": "user",
                    "content": _evidence_prompt(question, citations),
                },
            ],
            temperature=0.0,
            json_mode=True,
        )
        data = _parse_json_object(raw, protocol="Evidence QA generator")
        try:
            return EvidenceQADraft.model_validate(data)
        except ValidationError as exc:
            raise ValueError("Evidence QA generator 返回非法结构") from exc


class StructuredClaimSupportVerifier:
    def __init__(self, chat: ChatPort) -> None:
        self._chat = chat

    def verify(
        self,
        claims: list[EvidenceQAClaim],
        citations: list[EvidenceQACitation],
    ) -> ClaimSupportResult:
        if not claims:
            return ClaimSupportResult(
                judgements=[],
                unsupported_claim_ids=[],
                valid=True,
            )
        raw = self._chat.chat(
            [
                {"role": "system", "content": _VERIFIER_SYSTEM},
                {
                    "role": "user",
                    "content": _verification_prompt(claims, citations),
                },
            ],
            temperature=0.0,
            json_mode=True,
        )
        data = _parse_json_object(raw, protocol="Claim support verifier")
        raw_judgements = data.get("judgements")
        if not isinstance(raw_judgements, list):
            raise ValueError("Claim support verifier 缺少 judgements 数组")
        try:
            judgements = [ClaimSupportJudgement.model_validate(item) for item in raw_judgements]
        except ValidationError as exc:
            raise ValueError("Claim support verifier 返回非法 judgement") from exc
        _validate_verifier_coverage(claims, citations, judgements)
        unsupported = sorted(
            judgement.claim_id for judgement in judgements if not judgement.supported
        )
        return ClaimSupportResult(
            judgements=judgements,
            unsupported_claim_ids=unsupported,
            valid=not unsupported,
        )


def _evidence_prompt(
    question: str,
    citations: list[EvidenceQACitation],
) -> str:
    evidence = "\n\n".join(
        f"[{citation.citation_id}] {citation.source_name} {citation.title}\n{citation.quote}"
        for citation in citations
    )
    return f"【证据】\n{evidence}\n\n【问题】\n{question}"


def _verification_prompt(
    claims: list[EvidenceQAClaim],
    citations: list[EvidenceQACitation],
) -> str:
    evidence = "\n\n".join(f"[{citation.citation_id}] {citation.quote}" for citation in citations)
    claim_text = "\n".join(
        f"[{claim.claim_id}] {claim.text}\n声明引用: {', '.join(claim.citation_ids)}"
        for claim in claims
    )
    return f"【证据】\n{evidence}\n\n【Claims】\n{claim_text}"


def _parse_json_object(raw: str, *, protocol: str) -> dict[str, Any]:
    if not raw or not raw.strip():
        raise ValueError(f"{protocol} 返回空响应")
    body = raw.strip()
    if body.startswith("```"):
        lines = body.splitlines()
        if lines and lines[0].strip().lower() in {"```", "```json"}:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        body = "\n".join(lines).strip()
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{protocol} 返回非法 JSON") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{protocol} 必须返回 JSON 对象")
    return data


def _validate_verifier_coverage(
    claims: list[EvidenceQAClaim],
    citations: list[EvidenceQACitation],
    judgements: list[ClaimSupportJudgement],
) -> None:
    expected_claim_ids = {claim.claim_id for claim in claims}
    actual_claim_ids = {judgement.claim_id for judgement in judgements}
    if actual_claim_ids != expected_claim_ids:
        raise ValueError("Claim support verifier 必须且只能覆盖全部 Claim")
    known_citations = {citation.citation_id for citation in citations}
    claims_by_id = {claim.claim_id: claim for claim in claims}
    for judgement in judgements:
        declared = set(claims_by_id[judgement.claim_id].citation_ids)
        used = set(judgement.citation_ids)
        if not used.issubset(known_citations):
            raise ValueError("Claim support verifier 引用了未知 Citation")
        if judgement.supported and not used.issubset(declared):
            raise ValueError("Claim support verifier 不能扩大 Claim 声明的引用范围")
