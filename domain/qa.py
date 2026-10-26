"""Evidence QA 范围、引用、Claim 与结构校验模型。"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Literal

from pydantic import Field, model_validator

from domain.models import BaseDomainModel

QACorpus = Literal["regulatory", "user_docs", "knowledge", "web"]
EvidenceQAStatus = Literal["answered", "partially_answered", "refused"]
EvidenceQAPresentationKind = Literal["text", "table", "flowchart"]
NodeKind = Literal["start", "action", "decision", "merge", "terminal"]
ClaimRepairStatus = Literal["not_needed", "repaired", "failed"]
ClaimRemovalReason = Literal[
    "uncited",
    "unknown_citation",
    "unsupported",
    "verification_error",
    "unknown_context",
]
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _normalize_flow_semantics(text: str) -> str:
    """Normalize condition evidence without inventing synonyms or unstated facts."""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(character for character in normalized if character.isalnum())


class EvidenceQAScope(BaseDomainModel):
    """由服务端鉴权结果构造的检索范围，不接受客户端注入归属字段。"""

    corpora: list[QACorpus] = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    document_ids: list[str] | None = None

    @model_validator(mode="after")
    def validate_scope(self) -> EvidenceQAScope:
        if len(self.corpora) != len(set(self.corpora)):
            raise ValueError("corpora 不能重复")
        if self.document_ids is not None and "user_docs" not in self.corpora:
            raise ValueError("document_ids 仅用于 user_docs")
        return self


class EvidenceQACitation(BaseDomainModel):
    """回答引用的不可变证据定位。"""

    citation_id: str = Field(min_length=1, max_length=100)
    corpus: QACorpus
    source_id: str = Field(min_length=1, max_length=500)
    source_name: str = Field(min_length=1, max_length=500)
    title: str = Field(default="", max_length=500)
    quote: str = Field(min_length=1, max_length=4000)
    source_url: str | None = Field(default=None, max_length=2000)
    source_version: str | None = Field(default=None, max_length=200)
    owner_id: str | None = None
    document_id: str | None = None
    document_version_id: str | None = None
    page_number: int | None = Field(default=None, ge=1)
    source_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    clause_id: str | None = None
    score: float = Field(default=0.0, ge=0.0)

    @model_validator(mode="after")
    def validate_source_location(self) -> EvidenceQACitation:
        if not self.quote.strip():
            raise ValueError("quote 不能为空白字符串")
        if self.source_sha256 is not None and not _SHA256_RE.fullmatch(self.source_sha256):
            raise ValueError("source_sha256 必须是 64 位小写十六进制")
        if self.corpus == "user_docs":
            required = {
                "document_id": self.document_id,
                "document_version_id": self.document_version_id,
                "page_number": self.page_number,
                "source_sha256": self.source_sha256,
            }
            missing = [key for key, value in required.items() if value is None]
            if missing:
                raise ValueError("user_docs 引用缺少字段: " + ", ".join(missing))
        if self.corpus == "knowledge" and not self.owner_id:
            raise ValueError("knowledge 引用必须记录 owner_id")
        if self.corpus == "web" and not self.source_url:
            raise ValueError("web 引用必须记录真实 URL")
        return self


class UserContextMessage(BaseDomainModel):
    """A user-provided condition with an auditable conversation message source."""

    message_id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=8000)
    is_current: bool = False


class EvidenceQAClaim(BaseDomainModel):
    """LLM 只能输出原子 Claim 和引用 ID，不直接输出自由长答案。"""

    claim_id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=2000)
    citation_ids: list[str] = Field(default_factory=list)
    condition_message_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_claim(self) -> EvidenceQAClaim:
        if not self.text.strip():
            raise ValueError("Claim text 不能为空白字符串")
        if len(self.citation_ids) != len(set(self.citation_ids)):
            raise ValueError("citation_ids 不能重复")
        if len(self.condition_message_ids) != len(set(self.condition_message_ids)):
            raise ValueError("condition_message_ids 不能重复")
        return self


class EvidenceQATableRow(BaseDomainModel):
    """表格中的非事实分组标签，以及支撑该行的已校验 Claim。"""

    label: str = Field(min_length=1, max_length=100)
    claim_ids: list[str] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_row(self) -> EvidenceQATableRow:
        if not self.label.strip():
            raise ValueError("表格行 label 不能为空白字符串")
        if len(self.claim_ids) != len(set(self.claim_ids)):
            raise ValueError("表格行 claim_ids 不能重复")
        return self


class EvidenceQATable(BaseDomainModel):
    """表格只负责组织 Claim，事实单元格始终由服务端渲染已验证 Claim 原文。"""

    title: str = Field(default="", max_length=200)
    rows: list[EvidenceQATableRow] = Field(min_length=1, max_length=20)


class EvidenceQAFlowNode(BaseDomainModel):
    """流程节点不接收自由标签，展示文本由 claim_ids 对应 Claim 生成。"""

    node_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
    kind: NodeKind = "action"
    claim_ids: list[str] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def validate_node(self) -> EvidenceQAFlowNode:
        if len(self.claim_ids) != len(set(self.claim_ids)):
            raise ValueError("流程节点 claim_ids 不能重复")
        return self


class EvidenceQAFlowEdge(BaseDomainModel):
    """流程连线只引用 Claim；分支条件必须能在这些 Claim 的文本中核验。"""

    source_node_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
    target_node_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
    condition: str = Field(default="", max_length=80)
    claim_ids: list[str] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def validate_edge(self) -> EvidenceQAFlowEdge:
        if self.source_node_id == self.target_node_id:
            raise ValueError("流程连线不能指向自身")
        if self.condition and not self.condition.strip():
            raise ValueError("流程连线 condition 不能只包含空白")
        if len(self.claim_ids) != len(set(self.claim_ids)):
            raise ValueError("流程连线 claim_ids 不能重复")
        return self


class EvidenceQAFlowchart(BaseDomainModel):
    """通用流程 DSL v2；缺少 v2 标记的旧线性 payload 会被安全升级。"""

    schema_version: Literal["2.0"] = "2.0"
    title: str = Field(default="", max_length=200)
    nodes: list[EvidenceQAFlowNode] = Field(min_length=2, max_length=24)
    edges: list[EvidenceQAFlowEdge] = Field(min_length=1, max_length=48)

    @model_validator(mode="before")
    @classmethod
    def upgrade_v1_payload(cls, value: Any) -> Any:
        """Infer kinds for the old kind-less v1 shape; explicit v2 remains strict."""
        if not isinstance(value, dict):
            return value
        version = value.get("schema_version")
        if version not in (None, "1.0"):
            return value
        raw_nodes = value.get("nodes")
        raw_edges = value.get("edges")
        if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
            return value

        node_payloads: list[dict[str, Any]] = []
        explicit_kinds: list[bool] = []
        for raw_node in raw_nodes:
            if isinstance(raw_node, EvidenceQAFlowNode):
                node_payloads.append(raw_node.model_dump())
                explicit_kinds.append("kind" in raw_node.model_fields_set)
            elif isinstance(raw_node, dict):
                node_payloads.append(dict(raw_node))
                explicit_kinds.append("kind" in raw_node)
            else:
                return value

        edge_payloads: list[dict[str, Any]] = []
        for raw_edge in raw_edges:
            if isinstance(raw_edge, EvidenceQAFlowEdge):
                edge_payloads.append(raw_edge.model_dump())
            elif isinstance(raw_edge, dict):
                edge_payloads.append(dict(raw_edge))
            else:
                return value

        node_ids = [node.get("node_id") for node in node_payloads]
        indegree = {node_id: 0 for node_id in node_ids if isinstance(node_id, str)}
        outdegree = {node_id: 0 for node_id in node_ids if isinstance(node_id, str)}
        for edge in edge_payloads:
            source = edge.get("source_node_id")
            target = edge.get("target_node_id")
            if isinstance(source, str) and source in outdegree:
                outdegree[source] += 1
            if isinstance(target, str) and target in indegree:
                indegree[target] += 1

        for node, has_explicit_kind in zip(node_payloads, explicit_kinds, strict=True):
            if has_explicit_kind:
                continue
            node_id = node.get("node_id")
            if not isinstance(node_id, str):
                continue
            incoming = indegree.get(node_id, 0)
            outgoing = outdegree.get(node_id, 0)
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

        upgraded = dict(value)
        upgraded["schema_version"] = "2.0"
        upgraded["nodes"] = node_payloads
        upgraded["edges"] = edge_payloads
        return upgraded

    @model_validator(mode="after")
    def validate_graph(self) -> EvidenceQAFlowchart:
        node_ids = [node.node_id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("流程节点 node_id 不能重复")
        known_nodes = set(node_ids)
        edge_keys: list[tuple[str, str, str]] = []
        adjacency: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
        incoming: dict[str, list[EvidenceQAFlowEdge]] = {node_id: [] for node_id in node_ids}
        outgoing: dict[str, list[EvidenceQAFlowEdge]] = {node_id: [] for node_id in node_ids}
        for edge in self.edges:
            if edge.source_node_id not in known_nodes or edge.target_node_id not in known_nodes:
                raise ValueError("流程连线引用了未知节点")
            key = (
                edge.source_node_id,
                edge.target_node_id,
                _normalize_flow_semantics(edge.condition),
            )
            edge_keys.append(key)
            adjacency[edge.source_node_id].append(edge.target_node_id)
            outgoing[edge.source_node_id].append(edge)
            incoming[edge.target_node_id].append(edge)
        if len(edge_keys) != len(set(edge_keys)):
            raise ValueError("流程连线不能重复")
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node_id: str) -> None:
            if node_id in visiting:
                raise ValueError("流程图必须是无环有向图")
            if node_id in visited:
                return
            visiting.add(node_id)
            for target_id in adjacency[node_id]:
                visit(target_id)
            visiting.remove(node_id)
            visited.add(node_id)

        for node_id in node_ids:
            visit(node_id)

        starts = [node for node in self.nodes if node.kind == "start"]
        if len(starts) != 1:
            raise ValueError("流程图必须且只能有一个 start 节点")
        start = starts[0]
        if incoming[start.node_id]:
            raise ValueError("start 节点不能有入边")
        if not outgoing[start.node_id]:
            raise ValueError("start 节点必须至少有一个出口")

        terminals = [node for node in self.nodes if node.kind == "terminal"]
        if not terminals:
            raise ValueError("流程图必须至少有一个 terminal 节点")
        for node in self.nodes:
            node_incoming = incoming[node.node_id]
            node_outgoing = outgoing[node.node_id]
            if node.kind == "decision":
                if len(node_outgoing) < 2:
                    raise ValueError("decision 节点必须至少有两个出口")
                conditions = [_normalize_flow_semantics(edge.condition) for edge in node_outgoing]
                if any(not condition for condition in conditions):
                    raise ValueError("decision 节点的每个出口都必须有非空 condition")
                if len(conditions) != len(set(conditions)):
                    raise ValueError("decision 节点的出口 condition 必须唯一")
            elif node.kind == "action" and len(node_outgoing) > 1:
                raise ValueError("action 节点最多只能有一个出口")
            elif node.kind == "merge":
                if len(node_incoming) < 2:
                    raise ValueError("merge 节点必须至少有两条入边")
                if len(node_outgoing) > 1:
                    raise ValueError("merge 节点最多只能有一个出口")
            elif node.kind == "terminal" and node_outgoing:
                raise ValueError("terminal 节点不能有出口")

        reachable: set[str] = set()
        pending = [start.node_id]
        while pending:
            current = pending.pop()
            if current in reachable:
                continue
            reachable.add(current)
            pending.extend(adjacency[current])
        unreachable = known_nodes - reachable
        if unreachable:
            raise ValueError("所有非 start 节点都必须可达: " + ", ".join(sorted(unreachable)))
        if not any(terminal.node_id in reachable for terminal in terminals):
            raise ValueError("流程图必须存在 start 到 terminal 的路径")
        return self

    def to_v1_payload(self) -> dict[str, Any]:
        """Return the former kind-less graph shape for legacy internal consumers."""
        return {
            "title": self.title,
            "nodes": [
                {"node_id": node.node_id, "claim_ids": list(node.claim_ids)} for node in self.nodes
            ],
            "edges": [
                {
                    "source_node_id": edge.source_node_id,
                    "target_node_id": edge.target_node_id,
                    "claim_ids": list(edge.claim_ids),
                }
                for edge in self.edges
            ],
        }


class EvidenceQAPresentation(BaseDomainModel):
    """经验证 Claim 的展示方式；不会接受模型生成的 HTML、SVG 或图片。"""

    kind: EvidenceQAPresentationKind = "text"
    table: EvidenceQATable | None = None
    flowchart: EvidenceQAFlowchart | None = None

    @model_validator(mode="after")
    def validate_kind_payload(self) -> EvidenceQAPresentation:
        if self.kind == "text" and (self.table is not None or self.flowchart is not None):
            raise ValueError("text 展示不能携带 table 或 flowchart")
        if self.kind == "table" and (self.table is None or self.flowchart is not None):
            raise ValueError("table 展示必须且只能携带 table")
        if self.kind == "flowchart" and (self.flowchart is None or self.table is not None):
            raise ValueError("flowchart 展示必须且只能携带 flowchart")
        return self


class ClaimCitationVerification(BaseDomainModel):
    """Claim 与 Citation 的结构覆盖报告。"""

    claim_count: int = Field(ge=0)
    cited_claim_count: int = Field(ge=0)
    coverage: float = Field(ge=0.0, le=1.0)
    uncited_claim_ids: list[str] = Field(default_factory=list)
    unknown_citation_ids: list[str] = Field(default_factory=list)
    unused_citation_ids: list[str] = Field(default_factory=list)
    valid: bool
    method: Literal["structural_v1"] = "structural_v1"

    @model_validator(mode="after")
    def validate_counts(self) -> ClaimCitationVerification:
        if self.cited_claim_count > self.claim_count:
            raise ValueError("cited_claim_count 不能大于 claim_count")
        expected = 1.0 if self.claim_count == 0 else self.cited_claim_count / self.claim_count
        if abs(self.coverage - expected) > 1e-9:
            raise ValueError("coverage 与 Claim 计数不一致")
        expected_valid = (
            self.coverage == 1.0 and not self.uncited_claim_ids and not self.unknown_citation_ids
        )
        if self.valid != expected_valid:
            raise ValueError("valid 与 Claim-Citation 覆盖结果不一致")
        return self


class ClaimSupportJudgement(BaseDomainModel):
    """独立验证器对单个 Claim 的证据支持判定。"""

    claim_id: str = Field(min_length=1, max_length=100)
    supported: bool
    citation_ids: list[str] = Field(default_factory=list)
    reason: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def validate_judgement(self) -> ClaimSupportJudgement:
        if len(self.citation_ids) != len(set(self.citation_ids)):
            raise ValueError("ClaimSupport citation_ids 不能重复")
        if self.supported and not self.citation_ids:
            raise ValueError("supported Claim 必须记录实际支持它的 citation_ids")
        if not self.supported and not self.reason.strip():
            raise ValueError("unsupported Claim 必须说明 reason")
        return self


class ClaimSupportResult(BaseDomainModel):
    """语义支持校验结果；结果层只能移除不受支持的 Claim，不能把它放行。"""

    judgements: list[ClaimSupportJudgement] = Field(default_factory=list)
    unsupported_claim_ids: list[str] = Field(default_factory=list)
    valid: bool
    method: Literal["independent_llm_v1"] = "independent_llm_v1"

    @model_validator(mode="after")
    def validate_result(self) -> ClaimSupportResult:
        claim_ids = [judgement.claim_id for judgement in self.judgements]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("ClaimSupport judgement claim_id 不能重复")
        expected_unsupported = sorted(
            judgement.claim_id for judgement in self.judgements if not judgement.supported
        )
        if self.unsupported_claim_ids != expected_unsupported:
            raise ValueError("unsupported_claim_ids 与 judgements 不一致")
        if self.valid != (not self.unsupported_claim_ids):
            raise ValueError("ClaimSupport valid 与 unsupported_claim_ids 不一致")
        return self


class ClaimRepairReport(BaseDomainModel):
    """结果层有限修复报告；只允许删除坏 Claim，不改写内容或补造引用。"""

    status: ClaimRepairStatus
    original_claim_count: int = Field(ge=0)
    kept_claim_ids: list[str] = Field(default_factory=list)
    removed_claim_ids: list[str] = Field(default_factory=list)
    removal_reasons: dict[str, list[ClaimRemovalReason]] = Field(default_factory=dict)
    method: Literal["bounded_filter_v1"] = "bounded_filter_v1"

    @model_validator(mode="after")
    def validate_report(self) -> ClaimRepairReport:
        if len(self.kept_claim_ids) != len(set(self.kept_claim_ids)):
            raise ValueError("kept_claim_ids 不能重复")
        if len(self.removed_claim_ids) != len(set(self.removed_claim_ids)):
            raise ValueError("removed_claim_ids 不能重复")
        if set(self.kept_claim_ids) & set(self.removed_claim_ids):
            raise ValueError("同一 Claim 不能同时保留和移除")
        if self.original_claim_count != len(self.kept_claim_ids) + len(self.removed_claim_ids):
            raise ValueError("original_claim_count 与保留/移除 Claim 数不一致")
        if set(self.removal_reasons) != set(self.removed_claim_ids):
            raise ValueError("removal_reasons 必须且只能覆盖 removed_claim_ids")
        if any(
            not reasons or len(reasons) != len(set(reasons))
            for reasons in self.removal_reasons.values()
        ):
            raise ValueError("每个移除 Claim 必须有不重复的 removal_reasons")
        if self.status == "not_needed":
            if self.removed_claim_ids:
                raise ValueError("not_needed 不能包含 removed_claim_ids")
        elif self.status == "repaired":
            if not self.kept_claim_ids or not self.removed_claim_ids:
                raise ValueError("repaired 必须同时包含保留和移除 Claim")
        elif self.kept_claim_ids or not self.removed_claim_ids:
            raise ValueError("failed 必须移除全部候选 Claim")
        return self


class EvidenceQADraft(BaseDomainModel):
    """LLM 结构化输出；最终答案由服务端基于已校验 Claim 渲染。"""

    status: EvidenceQAStatus
    claims: list[EvidenceQAClaim] = Field(default_factory=list)
    refusal_reason: str = Field(default="", max_length=2000)
    unanswered_aspects: list[str] = Field(default_factory=list)
    presentation: EvidenceQAPresentation | None = None

    @model_validator(mode="after")
    def validate_draft(self) -> EvidenceQADraft:
        claim_ids = [claim.claim_id for claim in self.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("claim_id 不能重复")
        if len(self.unanswered_aspects) != len(set(self.unanswered_aspects)):
            raise ValueError("unanswered_aspects 不能重复")
        if self.status == "answered":
            if not self.claims:
                raise ValueError("answered 必须至少包含一个 Claim")
            if self.refusal_reason or self.unanswered_aspects:
                raise ValueError("answered 不能包含拒答原因或未回答部分")
        elif self.status == "partially_answered":
            if not self.claims or not self.unanswered_aspects:
                raise ValueError("partially_answered 必须包含 Claim 和未回答部分")
            if self.refusal_reason:
                raise ValueError("partially_answered 不使用 refusal_reason")
        else:
            if not self.refusal_reason.strip():
                raise ValueError("refused 必须说明 refusal_reason")
            if self.claims or self.unanswered_aspects:
                raise ValueError("refused 不能携带事实 Claim 或未回答部分")
            if self.presentation is not None:
                raise ValueError("refused 不能携带 presentation")
        _validate_presentation_claim_refs(self.presentation, self.claims)
        return self


class EvidenceQAAnswer(BaseDomainModel):
    """经过结构校验后的最终 Evidence QA 结果。"""

    question: str = Field(min_length=1, max_length=2000)
    scope: EvidenceQAScope
    status: EvidenceQAStatus
    claims: list[EvidenceQAClaim] = Field(default_factory=list)
    citations: list[EvidenceQACitation] = Field(default_factory=list)
    refusal_reason: str = Field(default="", max_length=2000)
    unanswered_aspects: list[str] = Field(default_factory=list)
    presentation: EvidenceQAPresentation | None = None
    verification: ClaimCitationVerification
    support_verification: ClaimSupportResult
    repair_report: ClaimRepairReport

    @model_validator(mode="after")
    def validate_answer(self) -> EvidenceQAAnswer:
        if not self.question.strip():
            raise ValueError("question 不能为空白字符串")
        draft = EvidenceQADraft(
            status=self.status,
            claims=self.claims,
            refusal_reason=self.refusal_reason,
            unanswered_aspects=self.unanswered_aspects,
            presentation=self.presentation,
        )
        citation_ids = [citation.citation_id for citation in self.citations]
        if len(citation_ids) != len(set(citation_ids)):
            raise ValueError("citation_id 不能重复")
        expected = ClaimCitationVerifier.verify(draft.claims, self.citations)
        if self.verification != expected:
            raise ValueError("verification 与当前 Claim-Citation 结构不一致")
        if self.status != "refused" and not self.verification.valid:
            raise ValueError("非拒答结果必须通过 Claim-Citation 校验")
        if self.status != "refused":
            expected_claim_ids = {claim.claim_id for claim in self.claims}
            actual_claim_ids = {
                judgement.claim_id for judgement in self.support_verification.judgements
            }
            if actual_claim_ids != expected_claim_ids:
                raise ValueError("support_verification 必须覆盖全部 Claim")
            if not self.support_verification.valid:
                raise ValueError("非拒答结果必须通过 Claim 语义支持校验")
            if self.repair_report.kept_claim_ids != [claim.claim_id for claim in self.claims]:
                raise ValueError("repair_report 必须按顺序记录最终保留的 Claim")
            if self.repair_report.status == "failed":
                raise ValueError("非拒答结果不能使用 failed repair_report")
            if self.repair_report.status == "repaired" and self.status != "partially_answered":
                raise ValueError("修复后的回答必须标记为 partially_answered")
        if self.status == "refused" and self.citations:
            raise ValueError("refused 不返回未被 Claim 使用的引用")
        if self.status == "refused" and self.support_verification.judgements:
            raise ValueError("refused 不返回 Claim 语义判定")
        if self.status == "refused" and self.repair_report.kept_claim_ids:
            raise ValueError("refused 的 repair_report 不能保留 Claim")
        if self.status == "refused" and self.repair_report.status == "repaired":
            raise ValueError("refused 不能使用 repaired repair_report")
        return self

    @property
    def answer(self) -> str:
        """只渲染通过校验的 Claim，避免 LLM 在自由长文本中夹带无引用结论。"""
        if self.status == "refused":
            return self.refusal_reason
        lines: list[str] = []
        if self.status == "partially_answered":
            lines.append("⚠️ 以下回答仅覆盖现有证据能够支持的部分：")
        presentation = self.presentation or EvidenceQAPresentation(kind="text")
        if presentation.kind == "table" and presentation.table is not None:
            lines.extend(_render_table(presentation.table, self.claims))
        elif presentation.kind == "flowchart" and presentation.flowchart is not None:
            lines.extend(
                _render_flowchart(
                    presentation.flowchart,
                    self.claims,
                    coverage="partial" if self.status == "partially_answered" else "complete",
                )
            )
        else:
            lines.extend(_render_claim_list(self.claims))
        if self.unanswered_aspects:
            lines.append("尚缺少证据：" + "；".join(self.unanswered_aspects))
        return "\n".join(lines)


def _presentation_claim_ids(presentation: EvidenceQAPresentation) -> list[str]:
    if presentation.kind == "table" and presentation.table is not None:
        return [claim_id for row in presentation.table.rows for claim_id in row.claim_ids]
    if presentation.kind == "flowchart" and presentation.flowchart is not None:
        node_claim_ids = [
            claim_id for node in presentation.flowchart.nodes for claim_id in node.claim_ids
        ]
        edge_claim_ids = [
            claim_id for edge in presentation.flowchart.edges for claim_id in edge.claim_ids
        ]
        return [*node_claim_ids, *edge_claim_ids]
    return []


def _validate_presentation_claim_refs(
    presentation: EvidenceQAPresentation | None,
    claims: list[EvidenceQAClaim],
) -> None:
    if presentation is None or presentation.kind == "text":
        return
    claims_by_id = {claim.claim_id: claim for claim in claims}
    known_claim_ids = set(claims_by_id)
    referenced = set(_presentation_claim_ids(presentation))
    unknown = referenced - known_claim_ids
    if unknown:
        raise ValueError("presentation 引用了未知 Claim: " + ", ".join(sorted(unknown)))
    missing = known_claim_ids - referenced
    if missing and presentation.kind == "table":
        raise ValueError("presentation 未覆盖 Claim: " + ", ".join(sorted(missing)))
    if presentation.kind == "flowchart" and presentation.flowchart is not None:
        for edge in presentation.flowchart.edges:
            normalized_condition = _normalize_flow_semantics(edge.condition)
            if normalized_condition and not any(
                normalized_condition in _normalize_flow_semantics(claims_by_id[claim_id].text)
                for claim_id in edge.claim_ids
            ):
                raise ValueError("流程连线 condition 无法从关联 Claim 文本中核验")


def _claim_citation_ids(
    claim_ids: list[str], claims_by_id: dict[str, EvidenceQAClaim]
) -> list[str]:
    citation_ids: list[str] = []
    for claim_id in claim_ids:
        for citation_id in claims_by_id[claim_id].citation_ids:
            if citation_id not in citation_ids:
                citation_ids.append(citation_id)
    return citation_ids


def _claim_text(claim_ids: list[str], claims_by_id: dict[str, EvidenceQAClaim]) -> str:
    return "；".join(claims_by_id[claim_id].text for claim_id in claim_ids)


def _flow_label(text: str, *, max_length: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_length:
        return normalized
    return normalized[: max_length - 1].rstrip() + "…"


def _markers(citation_ids: list[str]) -> str:
    return "".join(f"[{citation_id}]" for citation_id in citation_ids)


def _render_claim_list(claims: list[EvidenceQAClaim]) -> list[str]:
    return [
        f"{index}. {claim.text}{_markers(claim.citation_ids)}"
        for index, claim in enumerate(claims, start=1)
    ]


def _escape_table_cell(text: str) -> str:
    return text.replace("|", r"\|").replace("\n", " ").strip()


def _render_table(table: EvidenceQATable, claims: list[EvidenceQAClaim]) -> list[str]:
    claims_by_id = {claim.claim_id: claim for claim in claims}
    lines: list[str] = []
    if table.title.strip():
        lines.append(f"### {table.title.strip()}")
    lines.extend(
        [
            "| 项目 | 经核验结论 | 证据 |",
            "| --- | --- | --- |",
        ]
    )
    for row in table.rows:
        citation_ids = _claim_citation_ids(row.claim_ids, claims_by_id)
        lines.append(
            "| "
            + " | ".join(
                [
                    _escape_table_cell(row.label),
                    _escape_table_cell(_claim_text(row.claim_ids, claims_by_id)),
                    _markers(citation_ids),
                ]
            )
            + " |"
        )
    return lines


def _render_flowchart(
    flowchart: EvidenceQAFlowchart,
    claims: list[EvidenceQAClaim],
    *,
    coverage: Literal["complete", "partial"],
) -> list[str]:
    claims_by_id = {claim.claim_id: claim for claim in claims}
    payload = {
        "schema_version": flowchart.schema_version,
        "coverage": coverage,
        "title": flowchart.title.strip() or "经核验流程",
        "nodes": [
            {
                "id": node.node_id,
                "kind": node.kind,
                "label": _flow_label(
                    _claim_text(node.claim_ids, claims_by_id),
                    max_length=240,
                ),
                "citation_ids": _claim_citation_ids(node.claim_ids, claims_by_id),
            }
            for node in flowchart.nodes
        ],
        "edges": [
            {
                "from": edge.source_node_id,
                "to": edge.target_node_id,
                "condition": edge.condition.strip(),
                "label": edge.condition.strip() or "进入下一步",
                "citation_ids": _claim_citation_ids(edge.claim_ids, claims_by_id),
            }
            for edge in flowchart.edges
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return [
        f"```riskpilot-flow\n{encoded}\n```",
        "流程依据：",
        *_render_claim_list(claims),
    ]


class ClaimCitationVerifier:
    """纯函数式结构校验器，不把 LLM 自报的引用视为可信。"""

    @staticmethod
    def verify(
        claims: list[EvidenceQAClaim],
        citations: list[EvidenceQACitation],
    ) -> ClaimCitationVerification:
        known_ids = {citation.citation_id for citation in citations}
        uncited = [claim.claim_id for claim in claims if not claim.citation_ids]
        referenced_ids = {citation_id for claim in claims for citation_id in claim.citation_ids}
        unknown = sorted(referenced_ids - known_ids)
        unused = sorted(known_ids - referenced_ids)
        cited_claim_count = sum(
            bool(claim.citation_ids) and set(claim.citation_ids).issubset(known_ids)
            for claim in claims
        )
        claim_count = len(claims)
        coverage = 1.0 if claim_count == 0 else cited_claim_count / claim_count
        return ClaimCitationVerification(
            claim_count=claim_count,
            cited_claim_count=cited_claim_count,
            coverage=coverage,
            uncited_claim_ids=uncited,
            unknown_citation_ids=unknown,
            unused_citation_ids=unused,
            valid=coverage == 1.0 and not uncited and not unknown,
        )
