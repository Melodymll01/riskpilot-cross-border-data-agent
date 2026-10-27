"""基于 ChatPort 的结构化 Evidence QA 生成与独立支持校验。"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from domain.qa import (
    ClaimSupportJudgement,
    ClaimSupportResult,
    EvidenceQACitation,
    EvidenceQAClaim,
    EvidenceQADraft,
    UserContextMessage,
)

if TYPE_CHECKING:
    from domain.ports import ChatPort, RunControlPort

logger = logging.getLogger(__name__)
_FLOW_TRANSITION_RE = re.compile(
    r"第[一二三四五六七八九十百\d]+步|首先|然后|随后|之后|下一步|接着|进入|转交|流转|"
    r"完成后|结束后|开始前|最终|\b(?:first|then|next|after|before|upon|when)\b"
)

_GENERATOR_SYSTEM = """你是 Evidence QA 的结构化 Claim 生成器。

你只能使用用户消息中由服务端提供的证据 ID（例如 SRC-abc123）和对应原文，
不得使用训练知识，也不得发明或改写证据 ID。
【用户条件】来自同一会话的用户消息，不是外部证据。需要使用某项用户条件时，
必须在 condition_message_ids 中记录对应 message_id（展示标签中的 U: 不是 ID 的一部分）；
较新的用户消息会修正较旧条件。
当前追问中的代词、数量变化和条件替换必须结合这些带来源的用户条件解释。
把答案拆成可独立核验的原子 Claim。每个 Claim 必须引用至少一个证据 ID。
证据不足时返回 refused；只能回答部分时返回 partially_answered 并列出 unanswered_aspects。
presentation 只决定如何组织已验证 Claim，不得在其中增加新的事实：
- 默认使用 text。
- 对比、分类、材料清单适合 table；每一行只提供非事实分组 label 和 claim_ids。
- 用户明确询问流程、步骤、路径或先后关系，且证据直接支持节点与连线时可使用 flowchart。
- flowchart 必须输出 schema_version="2.0"。节点 kind 只能是
  start/action/decision/merge/terminal。同一已验证 Claim 可以支持多个图元素，但不得因此新增事实。
- 流程图最多 24 个节点、48 条边，只画核心阶段；相邻动作应合并成一个节点。材料清单、
  背景说明和补充事实保留在 claims/流程依据中，不得为了覆盖所有 Claim 把图无限拆细。
- start 唯一、无入边且有出口；decision 至少两个出口，且每个出口都有唯一的非空 condition；
  merge 至少两条入边且最多一个出口；terminal 无出口；所有节点从 start 可达，整体为 DAG，
  并且至少存在一条 start 到 terminal 的路径。
- 节点和边只能填写 claim_ids，不能填写自由事实标签。每条边必须引用直接支持该流转关系的 Claim。
  condition 最长 80 个字符，并且去除大小写、全半角、标点和空白差异后，必须原样出现在
  该边 claim_ids 对应的 Claim 文本中；不得概括、同义改写或补造条件。
- 若旧的线性 v1 输出缺少 kind，服务端可做兼容升级；新的分支流程不得省略 kind 或 condition。
- table 必须覆盖全部 claims；flowchart 可以只引用与核心流程结构有关的 Claim，未进入图的
  已验证 Claim 仍由服务端显示在“流程依据”中。不确定时使用 text。
不要输出思维链、Markdown 或 JSON 之外的文本。

输出 JSON：
{
  "status": "answered" | "partially_answered" | "refused",
  "claims": [
    {
      "claim_id": "C1",
      "text": "...",
      "citation_ids": ["SRC-abc123"],
      "condition_message_ids": ["msg_123"]
    }
  ],
  "presentation": {
    "kind": "text" | "table" | "flowchart",
    "table": {
      "title": "...",
      "rows": [{"label": "类别 A", "claim_ids": ["C1"]}]
    } | null,
    "flowchart": {
      "schema_version": "2.0",
      "title": "...",
      "nodes": [
        {"node_id": "N1", "kind": "start", "claim_ids": ["C1"]},
        {"node_id": "N2", "kind": "terminal", "claim_ids": ["C2"]}
      ],
      "edges": [
        {"source_node_id": "N1", "target_node_id": "N2", "condition": "", "claim_ids": ["C2"]}
      ]
    } | null
  },
  "refusal_reason": "",
  "unanswered_aspects": []
}
"""

_VERIFIER_SYSTEM = """你是独立的 Claim-Citation 语义支持校验器。

逐个判断 Claim 是否能由它声明引用的证据原文直接支持。不要因为 Claim 看起来合理就通过，
不要使用训练知识，不要补充推理链。任何事实、规则、状态或流程都必须由 Citation 支持；
具体场景假设只能来自 Claim 声明的用户消息。较新用户消息对旧条件的明确纠正优先。只返回 JSON。

输出 JSON：
{
  "judgements": [
    {
      "claim_id": "C1",
      "supported": true,
      "citation_ids": ["SRC-abc123"],
      "reason": ""
    }
  ]
}
"""

_PRESENTATION_REPAIR_SYSTEM = """你是流程展示结构修复器。

你只能重新组织输入中已有的 claim_id，不能新增、删除或改写 Claim，不能新增事实或证据。
根据 validation_errors 修复 presentation，并只返回 JSON 对象：
{"presentation": {"kind": "flowchart", "table": null, "flowchart": {...}}}

flowchart 必须使用 schema_version="2.0"，最多 24 个节点、48 条边；节点类型只能是
start/action/decision/merge/terminal。只能有一个 start，至少一个 terminal，整体必须是 DAG。
返工、补正、重试等循环关系写入相关 action 节点的 Claim 文本，不得画返回前序节点的边。
decision 至少两个带唯一非空 condition 的出口；merge 至少两个入口且最多一个出口；
terminal 不得有出口。condition 必须原样出现在该边引用的 Claim 文本中。
流程图可只使用核心流程 Claim，未进入图的 Claim 会在流程依据中继续展示。
无法得到合法流程图时返回 {"presentation": {"kind": "text", "table": null, "flowchart": null}}。
"""


class StructuredEvidenceQAGenerator:
    def __init__(self, chat: ChatPort) -> None:
        self._chat = chat

    def generate(
        self,
        *,
        question: str,
        citations: list[EvidenceQACitation],
        user_context: list[UserContextMessage] | None = None,
        preferences: list[str] | None = None,
        runtime_control: RunControlPort | None = None,
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
                    "content": _evidence_prompt(question, citations, user_context or [])
                    + "\n【用户表达偏好：仅用于语言和详略，不是事实或外部证据】\n"
                    + json.dumps(preferences or [], ensure_ascii=False),
                },
            ],
            temperature=0.0,
            max_tokens=3072,
            json_mode=True,
            runtime_control=runtime_control,
            operation="generation",
        )
        data = _parse_json_object(raw, protocol="Evidence QA generator")
        _normalize_flowchart_edge_claims(data)
        _dedupe_v2_edges(data)
        _normalize_v2_flowchart_kinds(data)
        _normalize_v2_conditions(data)
        if _dedupe_v2_decision_conditions(data):
            _mark_presentation_partial(
                data,
                "重复条件分支已合并，流程图仅保留可区分的证据分支。",
            )
            _normalize_v2_flowchart_kinds(data)
        if _prune_v2_to_primary_component(data):
            _mark_presentation_partial(
                data,
                "互不连通的次要分支已保留在流程依据中，流程图仅展示最大可达主路径。",
            )
            _normalize_v2_flowchart_kinds(data)
        try:
            return EvidenceQADraft.model_validate(data)
        except ValidationError as exc:
            if data.get("presentation") is not None:
                text_fallback = dict(data)
                text_fallback["presentation"] = None
                try:
                    draft = EvidenceQADraft.model_validate(text_fallback)
                except ValidationError:
                    pass
                else:
                    errors = [
                        {
                            "loc": ".".join(str(part) for part in item["loc"]),
                            "type": item["type"],
                            "message": item["msg"],
                        }
                        for item in exc.errors(include_input=False)
                    ]
                    repaired = self._repair_presentation(
                        question=question,
                        draft=draft,
                        invalid_presentation=data.get("presentation"),
                        errors=errors,
                        runtime_control=runtime_control,
                    )
                    if repaired is not None:
                        return repaired
                    logger.warning(
                        f"Evidence QA presentation 非法，已降级为文本展示 errors={errors}"
                    )
                    return draft
            raise ValueError("Evidence QA generator 返回非法结构") from exc

    def _repair_presentation(
        self,
        *,
        question: str,
        draft: EvidenceQADraft,
        invalid_presentation: object,
        errors: list[dict[str, str]],
        runtime_control: RunControlPort | None,
    ) -> EvidenceQADraft | None:
        try:
            raw = self._chat.chat(
                [
                    {"role": "system", "content": _PRESENTATION_REPAIR_SYSTEM},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "question": question,
                                "claims": [claim.model_dump() for claim in draft.claims],
                                "invalid_presentation": invalid_presentation,
                                "validation_errors": errors,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                temperature=0.0,
                max_tokens=3072,
                json_mode=True,
                runtime_control=runtime_control,
                operation="generation",
            )
            repaired_data = _parse_json_object(raw, protocol="Evidence QA presentation repair")
            presentation = repaired_data.get("presentation", repaired_data)
            candidate = {**draft.model_dump(), "presentation": presentation}
            removed_cycle_edges = _remove_v2_cycle_edges(candidate)
            _dedupe_v2_edges(candidate)
            if removed_cycle_edges:
                _mark_presentation_partial(
                    candidate,
                    "循环或返工关系已保留在流程依据中，DAG 流程图仅展示无环主路径。",
                )
            _normalize_v2_flowchart_kinds(candidate)
            _normalize_v2_conditions(candidate)
            if _dedupe_v2_decision_conditions(candidate):
                _mark_presentation_partial(
                    candidate,
                    "重复条件分支已合并，流程图仅保留可区分的证据分支。",
                )
                _normalize_v2_flowchart_kinds(candidate)
            if _prune_v2_to_primary_component(candidate):
                _mark_presentation_partial(
                    candidate,
                    "互不连通的次要分支已保留在流程依据中，流程图仅展示最大可达主路径。",
                )
                _normalize_v2_flowchart_kinds(candidate)
            return EvidenceQADraft.model_validate(candidate)
        except (RuntimeError, ValueError, ValidationError) as repair_error:
            detail = (
                repair_error.errors(include_input=False)
                if isinstance(repair_error, ValidationError)
                else str(repair_error)
            )
            logger.warning(
                f"Evidence QA presentation 修复失败，继续降级文本 "
                f"error={type(repair_error).__name__} detail={detail}"
            )
            return None


class StructuredClaimSupportVerifier:
    def __init__(self, chat: ChatPort) -> None:
        self._chat = chat

    def verify(
        self,
        claims: list[EvidenceQAClaim],
        citations: list[EvidenceQACitation],
        user_context: list[UserContextMessage] | None = None,
        runtime_control: RunControlPort | None = None,
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
                    "content": _verification_prompt(claims, citations, user_context or []),
                },
            ],
            temperature=0.0,
            max_tokens=3072,
            json_mode=True,
            runtime_control=runtime_control,
            operation="verification",
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
    user_context: list[UserContextMessage],
) -> str:
    evidence = "\n\n".join(
        f"[{citation.citation_id}] {citation.source_name} {citation.title}\n{citation.quote}"
        for citation in citations
    )
    context = _context_prompt(user_context)
    presentation_hint = _presentation_hint(question)
    return (
        f"【用户条件】\n{context}\n\n【证据】\n{evidence}\n\n"
        f"【当前问题】\n{question}\n\n【建议展示方式】\n{presentation_hint}"
    )


def _presentation_hint(question: str) -> str:
    if any(term in question for term in ("流程图", "画流程", "流程", "步骤")):
        return "flowchart；若证据不能直接支持节点与连线关系，则降级为 text"
    if any(term in question for term in ("表格", "对比", "比较", "区别", "清单")):
        return "table；若无法用已验证 Claim 完整组织，则降级为 text"
    return "由证据结构决定；默认 text"


def _verification_prompt(
    claims: list[EvidenceQAClaim],
    citations: list[EvidenceQACitation],
    user_context: list[UserContextMessage],
) -> str:
    evidence = "\n\n".join(f"[{citation.citation_id}] {citation.quote}" for citation in citations)
    claim_text = "\n".join(
        f"[{claim.claim_id}] {claim.text}\n声明引用: {', '.join(claim.citation_ids)}"
        f"\n声明用户条件: {', '.join(claim.condition_message_ids)}"
        for claim in claims
    )
    context = _context_prompt(user_context)
    return f"【用户条件】\n{context}\n\n【证据】\n{evidence}\n\n【Claims】\n{claim_text}"


def _context_prompt(user_context: list[UserContextMessage]) -> str:
    if not user_context:
        return "（无可用的会话用户条件）"
    return "\n".join(
        f"[U:{item.message_id}]{'（当前）' if item.is_current else ''} {item.text}"
        for item in user_context
    )


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


def _normalize_flowchart_edge_claims(data: dict[str, Any]) -> None:
    """Preserve the real-model v1 normalization path without rewriting valid v2 graphs."""
    presentation = data.get("presentation")
    if not isinstance(presentation, dict) or presentation.get("kind") != "flowchart":
        return
    flowchart = presentation.get("flowchart")
    if not isinstance(flowchart, dict):
        return
    nodes = flowchart.get("nodes")
    edges = flowchart.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return
    legacy = flowchart.get("schema_version") == "1.0" or (
        flowchart.get("schema_version") is None
        and any(isinstance(node, dict) and "kind" not in node for node in nodes)
    )
    if not legacy:
        if flowchart.get("schema_version") is None:
            flowchart["schema_version"] = "2.0"
        return
    claim_texts: dict[str, str] = {}
    raw_claims = data.get("claims")
    if isinstance(raw_claims, list):
        for claim in raw_claims:
            if not isinstance(claim, dict):
                continue
            claim_id = claim.get("claim_id")
            text = claim.get("text")
            if isinstance(claim_id, str) and isinstance(text, str):
                claim_texts[claim_id] = text
    target_claims: dict[str, list[str]] = {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = node.get("node_id")
        claim_ids = node.get("claim_ids")
        if (
            isinstance(node_id, str)
            and isinstance(claim_ids, list)
            and all(isinstance(claim_id, str) for claim_id in claim_ids)
        ):
            target_claims[node_id] = list(claim_ids)
    normalized_edges: list[dict[str, Any]] = []
    used_node_ids: set[str] = set()
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source_node_id = edge.get("source_node_id")
        target_node_id = edge.get("target_node_id")
        if (
            not isinstance(source_node_id, str)
            or not isinstance(target_node_id, str)
            or target_node_id not in target_claims
        ):
            continue
        inherited_claim_ids = target_claims[target_node_id]
        if not any(
            _FLOW_TRANSITION_RE.search(claim_texts.get(claim_id, ""))
            for claim_id in inherited_claim_ids
        ):
            continue
        edge["claim_ids"] = list(inherited_claim_ids)
        normalized_edges.append(edge)
        used_node_ids.update((source_node_id, target_node_id))
    flowchart["edges"] = normalized_edges
    flowchart["nodes"] = [
        node for node in nodes if isinstance(node, dict) and node.get("node_id") in used_node_ids
    ]


def _normalize_v2_flowchart_kinds(data: dict[str, Any]) -> None:
    """节点 kind 由已生成拓扑确定，避免模型把分支/汇合类型标反。"""
    presentation = data.get("presentation")
    if not isinstance(presentation, dict) or presentation.get("kind") != "flowchart":
        return
    flowchart = presentation.get("flowchart")
    if not isinstance(flowchart, dict) or flowchart.get("schema_version") != "2.0":
        return
    nodes = flowchart.get("nodes")
    edges = flowchart.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return
    indegree: dict[str, int] = {}
    outdegree: dict[str, int] = {}
    for node in nodes:
        if isinstance(node, dict) and isinstance(node.get("node_id"), str):
            node_id = node["node_id"]
            indegree[node_id] = 0
            outdegree[node_id] = 0
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source = edge.get("source_node_id")
        target = edge.get("target_node_id")
        if isinstance(source, str) and source in outdegree:
            outdegree[source] += 1
        if isinstance(target, str) and target in indegree:
            indegree[target] += 1
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = node.get("node_id")
        if not isinstance(node_id, str) or node_id not in indegree:
            continue
        incoming = indegree[node_id]
        outgoing = outdegree[node_id]
        if incoming == 0:
            node["kind"] = "start"
        elif outgoing == 0:
            node["kind"] = "terminal"
        elif outgoing >= 2:
            node["kind"] = "decision"
        elif incoming >= 2:
            node["kind"] = "merge"
        else:
            node["kind"] = "action"


def _remove_v2_cycle_edges(data: dict[str, Any]) -> int:
    """按模型给定顺序保留无环边；只删除回边，不创建新的流程关系。"""
    presentation = data.get("presentation")
    if not isinstance(presentation, dict) or presentation.get("kind") != "flowchart":
        return 0
    flowchart = presentation.get("flowchart")
    if not isinstance(flowchart, dict) or flowchart.get("schema_version") != "2.0":
        return 0
    nodes = flowchart.get("nodes")
    edges = flowchart.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return 0
    node_ids: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = node.get("node_id")
        if isinstance(node_id, str):
            node_ids.add(node_id)
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in node_ids}

    def reaches(start: str, target: str) -> bool:
        pending = [start]
        seen: set[str] = set()
        while pending:
            current = pending.pop()
            if current == target:
                return True
            if current in seen:
                continue
            seen.add(current)
            pending.extend(adjacency.get(current, ()))
        return False

    kept: list[dict[str, Any]] = []
    removed = 0
    for edge in edges:
        if not isinstance(edge, dict):
            kept.append(edge)
            continue
        source = edge.get("source_node_id")
        target = edge.get("target_node_id")
        if not isinstance(source, str) or not isinstance(target, str):
            kept.append(edge)
            continue
        if source in node_ids and target in node_ids and reaches(target, source):
            removed += 1
            continue
        kept.append(edge)
        if source in adjacency and target in node_ids:
            adjacency[source].add(target)
    flowchart["edges"] = kept
    return removed


def _normalize_v2_conditions(data: dict[str, Any]) -> None:
    """条件标签只能使用关联 Claim 的原文片段；非分支边统一清空。"""
    presentation = data.get("presentation")
    if not isinstance(presentation, dict) or presentation.get("kind") != "flowchart":
        return
    flowchart = presentation.get("flowchart")
    if not isinstance(flowchart, dict) or flowchart.get("schema_version") != "2.0":
        return
    nodes = flowchart.get("nodes")
    edges = flowchart.get("edges")
    claims = data.get("claims")
    if not isinstance(nodes, list) or not isinstance(edges, list) or not isinstance(claims, list):
        return
    claims_by_id: dict[str, str] = {}
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        claim_id = claim.get("claim_id")
        claim_text = claim.get("text")
        if isinstance(claim_id, str) and isinstance(claim_text, str):
            claims_by_id[claim_id] = claim_text
    node_kinds = {
        node.get("node_id"): node.get("kind")
        for node in nodes
        if isinstance(node, dict) and isinstance(node.get("node_id"), str)
    }
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source = edge.get("source_node_id")
        condition = edge.get("condition")
        claim_ids = edge.get("claim_ids")
        if not isinstance(claim_ids, list):
            continue
        texts = [
            claims_by_id[claim_id]
            for claim_id in claim_ids
            if isinstance(claim_id, str) and claim_id in claims_by_id
        ]
        normalized_condition = _normalize_flow_text(condition) if isinstance(condition, str) else ""
        if normalized_condition and any(
            normalized_condition in _normalize_flow_text(text) for text in texts
        ):
            continue
        if node_kinds.get(source) != "decision":
            edge["condition"] = ""
            continue
        derived = _condition_fragment(texts)
        if derived:
            edge["condition"] = derived


def _normalize_flow_text(text: str) -> str:
    return "".join(character for character in text.casefold() if character.isalnum())


def _condition_fragment(texts: list[str]) -> str:
    for text in texts:
        fragment = re.split(r"[，,。；;：:\n]", text, maxsplit=1)[0].strip()
        if fragment:
            return fragment[:32].rstrip()
    return ""


def _dedupe_v2_edges(data: dict[str, Any]) -> int:
    """合并完全重复的 v2 连线；重复本身不改变流程语义。"""
    flowchart = _v2_flowchart_payload(data)
    if flowchart is None:
        return 0
    edges = flowchart.get("edges")
    if not isinstance(edges, list):
        return 0
    seen: set[tuple[str, str, str]] = set()
    kept: list[object] = []
    removed = 0
    for edge in edges:
        if not isinstance(edge, dict):
            kept.append(edge)
            continue
        source = edge.get("source_node_id")
        target = edge.get("target_node_id")
        condition = edge.get("condition")
        if not isinstance(source, str) or not isinstance(target, str):
            kept.append(edge)
            continue
        key = (
            source,
            target,
            _normalize_flow_text(condition) if isinstance(condition, str) else "",
        )
        if key in seen:
            removed += 1
            continue
        seen.add(key)
        kept.append(edge)
    flowchart["edges"] = kept
    return removed


def _dedupe_v2_decision_conditions(data: dict[str, Any]) -> int:
    """同一分支点的条件必须可区分；重复条件只保留首条证据边。"""
    flowchart = _v2_flowchart_payload(data)
    if flowchart is None:
        return 0
    nodes = flowchart.get("nodes")
    edges = flowchart.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return 0
    decision_ids: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict) or node.get("kind") != "decision":
            continue
        node_id = node.get("node_id")
        if isinstance(node_id, str):
            decision_ids.add(node_id)
    seen_by_source: dict[str, set[str]] = {node_id: set() for node_id in decision_ids}
    kept: list[object] = []
    removed = 0
    for edge in edges:
        if not isinstance(edge, dict):
            kept.append(edge)
            continue
        source = edge.get("source_node_id")
        condition = edge.get("condition")
        if (
            not isinstance(source, str)
            or source not in decision_ids
            or not isinstance(condition, str)
        ):
            kept.append(edge)
            continue
        normalized = _normalize_flow_text(condition)
        if normalized and normalized in seen_by_source[source]:
            removed += 1
            continue
        if normalized:
            seen_by_source[source].add(normalized)
        kept.append(edge)
    flowchart["edges"] = kept
    return removed


def _v2_flowchart_payload(data: dict[str, Any]) -> dict[str, Any] | None:
    presentation = data.get("presentation")
    if not isinstance(presentation, dict) or presentation.get("kind") != "flowchart":
        return None
    flowchart = presentation.get("flowchart")
    if not isinstance(flowchart, dict) or flowchart.get("schema_version") != "2.0":
        return None
    return flowchart


def _mark_presentation_partial(data: dict[str, Any], reason: str) -> None:
    if data.get("status") == "answered":
        data["status"] = "partially_answered"
    aspects = data.get("unanswered_aspects")
    if not isinstance(aspects, list):
        aspects = []
        data["unanswered_aspects"] = aspects
    if reason not in aspects:
        aspects.append(reason)


def _prune_v2_to_primary_component(data: dict[str, Any]) -> int:
    """多根图只保留最大可达组件；不连接原本没有证据关系的分支。"""
    flowchart = _v2_flowchart_payload(data)
    if flowchart is None:
        return 0
    nodes = flowchart.get("nodes")
    edges = flowchart.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return 0
    ordered_ids: list[str] = []
    explicit_starts: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = node.get("node_id")
        if not isinstance(node_id, str):
            continue
        ordered_ids.append(node_id)
        if node.get("kind") == "start":
            explicit_starts.add(node_id)
    adjacency: dict[str, list[str]] = {node_id: [] for node_id in ordered_ids}
    indegree: dict[str, int] = dict.fromkeys(ordered_ids, 0)
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source = edge.get("source_node_id")
        target = edge.get("target_node_id")
        if (
            isinstance(source, str)
            and isinstance(target, str)
            and source in adjacency
            and target in indegree
        ):
            adjacency[source].append(target)
            indegree[target] += 1
    roots = [node_id for node_id in ordered_ids if indegree[node_id] == 0]
    if len(roots) <= 1:
        return 0

    def reachable(root: str) -> set[str]:
        found: set[str] = set()
        pending = [root]
        while pending:
            current = pending.pop()
            if current in found:
                continue
            found.add(current)
            pending.extend(adjacency.get(current, ()))
        return found

    candidates = [root for root in roots if root in explicit_starts] or roots
    primary = max(candidates, key=lambda root: (len(reachable(root)), -ordered_ids.index(root)))
    keep_ids = reachable(primary)
    original_node_count = len(nodes)
    flowchart["nodes"] = [
        node for node in nodes if isinstance(node, dict) and node.get("node_id") in keep_ids
    ]
    flowchart["edges"] = [
        edge
        for edge in edges
        if isinstance(edge, dict)
        and edge.get("source_node_id") in keep_ids
        and edge.get("target_node_id") in keep_ids
    ]
    return original_node_count - len(flowchart["nodes"])


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
