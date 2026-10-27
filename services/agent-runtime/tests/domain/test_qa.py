"""Evidence QA 范围、引用与 Claim-Citation 校验测试。"""

from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from domain.qa import (
    ClaimCitationVerifier,
    ClaimRepairReport,
    ClaimSupportJudgement,
    ClaimSupportResult,
    EvidenceQAAnswer,
    EvidenceQACitation,
    EvidenceQAClaim,
    EvidenceQADraft,
    EvidenceQAFlowchart,
    EvidenceQAFlowEdge,
    EvidenceQAFlowNode,
    EvidenceQAPresentation,
    EvidenceQAScope,
    EvidenceQATable,
    EvidenceQATableRow,
)


def _case_citation(**overrides: object) -> EvidenceQACitation:
    values: dict[str, object] = {
        "citation_id": "E1",
        "corpus": "user_docs",
        "source_id": "chunk_001",
        "source_name": "case.txt",
        "quote": "境外接收方应承担安全保护责任",
        "document_id": "doc_001",
        "document_version_id": "ver_001",
        "page_number": 2,
        "source_sha256": "a" * 64,
        "score": 0.9,
    }
    values.update(overrides)
    return EvidenceQACitation(**values)  # type: ignore[arg-type]


def _claim(**overrides: object) -> EvidenceQAClaim:
    values: dict[str, object] = {
        "claim_id": "C1",
        "text": "境外接收方应承担安全保护责任。",
        "citation_ids": ["E1"],
    }
    values.update(overrides)
    return EvidenceQAClaim(**values)  # type: ignore[arg-type]


def _contract_claims() -> list[EvidenceQAClaim]:
    texts = {
        "C1": "申请人提交合同后，流程进入初审。",
        "C2": "初审人员检查合同信息。",
        "C3": "初审完成后，系统判断审批结果。",
        "C4": "审批通过后，合同进入归档队列。",
        "C5": "审批退回后，合同交由申请人修改。",
        "C6": "通过与退回分支汇合后，系统记录处理结果。",
        "C7": "记录完成后，合同审批流程终止。",
    }
    return [
        EvidenceQAClaim(claim_id=claim_id, text=text, citation_ids=["E1"])
        for claim_id, text in texts.items()
    ]


def _contract_flow_payload() -> dict[str, object]:
    return {
        "schema_version": "2.0",
        "title": "合同审批",
        "nodes": [
            {"node_id": "N1", "kind": "start", "claim_ids": ["C1"]},
            {"node_id": "N2", "kind": "action", "claim_ids": ["C2"]},
            {"node_id": "N3", "kind": "decision", "claim_ids": ["C3"]},
            {"node_id": "N4", "kind": "action", "claim_ids": ["C4"]},
            {"node_id": "N5", "kind": "action", "claim_ids": ["C5"]},
            {"node_id": "N6", "kind": "merge", "claim_ids": ["C6"]},
            {"node_id": "N7", "kind": "terminal", "claim_ids": ["C7"]},
        ],
        "edges": [
            {"source_node_id": "N1", "target_node_id": "N2", "claim_ids": ["C1"]},
            {"source_node_id": "N2", "target_node_id": "N3", "claim_ids": ["C3"]},
            {
                "source_node_id": "N3",
                "target_node_id": "N4",
                "condition": "审批通过",
                "claim_ids": ["C4"],
            },
            {
                "source_node_id": "N3",
                "target_node_id": "N5",
                "condition": "审批退回",
                "claim_ids": ["C5"],
            },
            {"source_node_id": "N4", "target_node_id": "N6", "claim_ids": ["C6"]},
            {"source_node_id": "N5", "target_node_id": "N6", "claim_ids": ["C6"]},
            {"source_node_id": "N6", "target_node_id": "N7", "claim_ids": ["C7"]},
        ],
    }


class TestEvidenceQAScope:
    def test_regulatory_scope_uses_server_owner(self) -> None:
        scope = EvidenceQAScope(owner_id="github:alice", corpora=["regulatory"])
        assert scope.owner_id == "github:alice"
        with pytest.raises(ValueError):
            EvidenceQAScope(owner_id="", corpora=["user_docs"])

    def test_document_filter_only_accepted_for_user_docs(self) -> None:
        with pytest.raises(ValueError):
            EvidenceQAScope(
                owner_id="github:alice", corpora=["regulatory"], document_ids=["private"]
            )

    def test_duplicate_corpora_rejected(self) -> None:
        with pytest.raises(ValidationError, match="corpora"):
            EvidenceQAScope(owner_id="github:alice", corpora=["regulatory", "regulatory"])
        with pytest.raises(ValidationError, match="assessment_id"):
            EvidenceQAScope(
                owner_id="github:alice",
                corpora=["user_docs"],
                assessment_id="assessment_001",
            )


class TestEvidenceQACitation:
    def test_case_citation_requires_versioned_page_location(self) -> None:
        with pytest.raises(ValidationError, match="page_number"):
            _case_citation(page_number=None)
        with pytest.raises(ValidationError, match="document_version_id"):
            _case_citation(document_version_id=None)
        with pytest.raises(ValidationError, match="source_sha256"):
            _case_citation(source_sha256=None)

    def test_blank_quote_rejected(self) -> None:
        with pytest.raises(ValidationError, match="quote"):
            _case_citation(quote=" ")


class TestEvidenceQADraft:
    def test_answered_requires_claim(self) -> None:
        with pytest.raises(ValidationError, match="Claim"):
            EvidenceQADraft(status="answered")

    def test_partial_requires_unanswered_aspects(self) -> None:
        with pytest.raises(ValidationError, match="未回答"):
            EvidenceQADraft(status="partially_answered", claims=[_claim()])

    def test_refused_cannot_smuggle_claims(self) -> None:
        with pytest.raises(ValidationError, match="不能携带"):
            EvidenceQADraft(
                status="refused",
                claims=[_claim()],
                refusal_reason="证据不足",
            )

    def test_duplicate_claim_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="claim_id"):
            EvidenceQADraft(status="answered", claims=[_claim(), _claim()])

    def test_table_presentation_must_cover_known_claims(self) -> None:
        second = _claim(claim_id="C2")
        presentation = EvidenceQAPresentation(
            kind="table",
            table=EvidenceQATable(rows=[EvidenceQATableRow(label="主体", claim_ids=["C1"])]),
        )
        with pytest.raises(ValidationError, match="未覆盖 Claim"):
            EvidenceQADraft(
                status="answered",
                claims=[_claim(), second],
                presentation=presentation,
            )

    def test_flowchart_rejects_cycles_and_unknown_nodes(self) -> None:
        with pytest.raises(ValidationError, match="未知节点"):
            EvidenceQAFlowchart(
                nodes=[
                    EvidenceQAFlowNode(node_id="N1", claim_ids=["C1"]),
                    EvidenceQAFlowNode(node_id="N2", claim_ids=["C2"]),
                ],
                edges=[
                    EvidenceQAFlowEdge(
                        source_node_id="N1",
                        target_node_id="N3",
                        claim_ids=["C3"],
                    )
                ],
            )
        with pytest.raises(ValidationError, match="无环"):
            EvidenceQAFlowchart(
                nodes=[
                    EvidenceQAFlowNode(node_id="N1", claim_ids=["C1"]),
                    EvidenceQAFlowNode(node_id="N2", claim_ids=["C2"]),
                ],
                edges=[
                    EvidenceQAFlowEdge(
                        source_node_id="N1",
                        target_node_id="N2",
                        claim_ids=["C2"],
                    ),
                    EvidenceQAFlowEdge(
                        source_node_id="N2",
                        target_node_id="N1",
                        claim_ids=["C1"],
                    ),
                ],
            )

    def test_flowchart_condition_must_match_associated_claim(self) -> None:
        presentation = EvidenceQAPresentation(
            kind="flowchart",
            flowchart=EvidenceQAFlowchart(
                nodes=[
                    EvidenceQAFlowNode(node_id="N1", kind="start", claim_ids=["C1"]),
                    EvidenceQAFlowNode(node_id="N2", kind="terminal", claim_ids=["C2"]),
                ],
                edges=[
                    EvidenceQAFlowEdge(
                        source_node_id="N1",
                        target_node_id="N2",
                        condition="审批通过",
                        claim_ids=["C2"],
                    )
                ],
            ),
        )
        with pytest.raises(ValidationError, match="condition 无法"):
            EvidenceQADraft(
                status="answered",
                claims=[_claim(), _claim(claim_id="C2", text="网信部门开展查验。")],
                presentation=presentation,
            )

    def test_generic_contract_flow_v2_is_valid_and_conditions_are_normalized(self) -> None:
        payload = _contract_flow_payload()
        payload["edges"][2]["condition"] = "审批 通 过"  # type: ignore[index]
        flowchart = EvidenceQAFlowchart.model_validate(payload)

        draft = EvidenceQADraft(
            status="answered",
            claims=_contract_claims(),
            presentation=EvidenceQAPresentation(kind="flowchart", flowchart=flowchart),
        )

        assert draft.presentation is not None
        assert flowchart.schema_version == "2.0"
        assert {node.kind for node in flowchart.nodes} == {
            "start",
            "action",
            "decision",
            "merge",
            "terminal",
        }

    @pytest.mark.parametrize(
        ("violation", "message"),
        [
            ("multiple_start", "只能有一个 start"),
            ("start_incoming", "start 节点不能有入边"),
            ("start_no_exit", "start 节点必须至少有一个出口"),
            ("decision_one_exit", "decision 节点必须至少有两个出口"),
            ("decision_empty_condition", "非空 condition"),
            ("decision_duplicate_condition", "condition 必须唯一"),
            ("action_two_exits", "action 节点最多只能有一个出口"),
            ("merge_one_input", "merge 节点必须至少有两条入边"),
            ("merge_two_exits", "merge 节点最多只能有一个出口"),
            ("terminal_exit", "terminal 节点不能有出口"),
            ("unreachable", "必须可达"),
            ("cycle", "无环有向图"),
            ("no_terminal", "至少有一个 terminal"),
        ],
    )
    def test_flow_v2_rejects_every_structural_violation(
        self,
        violation: str,
        message: str,
    ) -> None:
        payload = deepcopy(_contract_flow_payload())
        nodes = payload["nodes"]
        edges = payload["edges"]
        assert isinstance(nodes, list)
        assert isinstance(edges, list)

        if violation == "multiple_start":
            nodes[1]["kind"] = "start"
        elif violation == "start_incoming":
            nodes.append({"node_id": "N8", "kind": "action", "claim_ids": ["C8"]})
            edges.append({"source_node_id": "N8", "target_node_id": "N1", "claim_ids": ["C8"]})
        elif violation == "start_no_exit":
            edges.pop(0)
        elif violation == "decision_one_exit":
            edges.pop(3)
        elif violation == "decision_empty_condition":
            edges[2]["condition"] = ""
        elif violation == "decision_duplicate_condition":
            edges[3]["condition"] = "审 批 通 过"
        elif violation == "action_two_exits":
            edges.append({"source_node_id": "N2", "target_node_id": "N4", "claim_ids": ["C4"]})
        elif violation == "merge_one_input":
            edges.pop(5)
        elif violation == "merge_two_exits":
            nodes.append({"node_id": "N8", "kind": "terminal", "claim_ids": ["C8"]})
            edges.append({"source_node_id": "N6", "target_node_id": "N8", "claim_ids": ["C8"]})
        elif violation == "terminal_exit":
            nodes.append({"node_id": "N8", "kind": "terminal", "claim_ids": ["C8"]})
            edges.append({"source_node_id": "N7", "target_node_id": "N8", "claim_ids": ["C8"]})
        elif violation == "unreachable":
            nodes.append({"node_id": "N8", "kind": "action", "claim_ids": ["C8"]})
        elif violation == "cycle":
            edges.append({"source_node_id": "N6", "target_node_id": "N3", "claim_ids": ["C6"]})
        elif violation == "no_terminal":
            nodes[6]["kind"] = "action"

        with pytest.raises(ValidationError, match=message):
            EvidenceQAFlowchart.model_validate(payload)

    @pytest.mark.parametrize("reference", ["node", "edge"])
    def test_flow_v2_rejects_unknown_claim_references(self, reference: str) -> None:
        payload = _contract_flow_payload()
        if reference == "node":
            payload["nodes"][1]["claim_ids"] = ["UNKNOWN"]  # type: ignore[index]
        else:
            payload["edges"][0]["claim_ids"] = ["UNKNOWN"]  # type: ignore[index]
        presentation = EvidenceQAPresentation(
            kind="flowchart",
            flowchart=EvidenceQAFlowchart.model_validate(payload),
        )

        with pytest.raises(ValidationError, match="未知 Claim"):
            EvidenceQADraft(
                status="answered",
                claims=_contract_claims(),
                presentation=presentation,
            )

    def test_v1_linear_payload_is_upgraded_and_can_be_exported(self) -> None:
        v1 = {
            "title": "事件上报",
            "nodes": [
                {"node_id": "N1", "claim_ids": ["C1"]},
                {"node_id": "N2", "claim_ids": ["C2"]},
            ],
            "edges": [{"source_node_id": "N1", "target_node_id": "N2", "claim_ids": ["C2"]}],
        }

        upgraded = EvidenceQAFlowchart.model_validate(v1)

        assert [node.kind for node in upgraded.nodes] == ["start", "terminal"]
        assert upgraded.to_v1_payload() == v1


def _support_result(
    *,
    supported: bool = True,
    claim_id: str = "C1",
    citation_ids: list[str] | None = None,
) -> ClaimSupportResult:
    judgement = ClaimSupportJudgement(
        claim_id=claim_id,
        supported=supported,
        citation_ids=(["E1"] if supported else []) if citation_ids is None else citation_ids,
        reason="" if supported else "原文不支持",
    )
    unsupported = [] if supported else [claim_id]
    return ClaimSupportResult(
        judgements=[judgement],
        unsupported_claim_ids=unsupported,
        valid=supported,
    )


def _repair_report(
    *,
    status: str = "not_needed",
    kept_claim_ids: list[str] | None = None,
    removed_claim_ids: list[str] | None = None,
    removal_reasons: dict[str, list[str]] | None = None,
) -> ClaimRepairReport:
    kept = ["C1"] if kept_claim_ids is None else kept_claim_ids
    removed = [] if removed_claim_ids is None else removed_claim_ids
    return ClaimRepairReport(
        status=status,  # type: ignore[arg-type]
        original_claim_count=len(kept) + len(removed),
        kept_claim_ids=kept,
        removed_claim_ids=removed,
        removal_reasons=removal_reasons or {},  # type: ignore[arg-type]
    )


class TestClaimCitationVerifier:
    def test_full_coverage_is_valid(self) -> None:
        verification = ClaimCitationVerifier.verify([_claim()], [_case_citation()])
        assert verification.valid is True
        assert verification.coverage == 1.0
        assert verification.uncited_claim_ids == []
        assert verification.unknown_citation_ids == []

    def test_uncited_claim_blocks_validity(self) -> None:
        verification = ClaimCitationVerifier.verify(
            [_claim(citation_ids=[])],
            [_case_citation()],
        )
        assert verification.valid is False
        assert verification.coverage == 0.0
        assert verification.uncited_claim_ids == ["C1"]
        assert verification.unused_citation_ids == ["E1"]

    def test_unknown_citation_id_blocks_validity(self) -> None:
        verification = ClaimCitationVerifier.verify(
            [_claim(citation_ids=["UNKNOWN"])],
            [_case_citation()],
        )
        assert verification.valid is False
        assert verification.unknown_citation_ids == ["UNKNOWN"]
        assert verification.coverage == 0.0

    def test_unused_citation_does_not_invalidate_supported_claim(self) -> None:
        verification = ClaimCitationVerifier.verify(
            [_claim()],
            [_case_citation(), _case_citation(citation_id="E2", source_id="chunk_002")],
        )
        assert verification.valid is True
        assert verification.unused_citation_ids == ["E2"]

    def test_empty_refusal_has_vacuous_structural_coverage(self) -> None:
        verification = ClaimCitationVerifier.verify([], [])
        assert verification.valid is True
        assert verification.coverage == 1.0


class TestClaimRepairReport:
    def test_repaired_requires_kept_and_removed_claims(self) -> None:
        report = _repair_report(
            status="repaired",
            kept_claim_ids=["C1"],
            removed_claim_ids=["C2"],
            removal_reasons={"C2": ["unsupported"]},
        )
        assert report.method == "bounded_filter_v1"

        with pytest.raises(ValidationError, match="同时包含"):
            _repair_report(
                status="repaired",
                kept_claim_ids=[],
                removed_claim_ids=["C1"],
                removal_reasons={"C1": ["unsupported"]},
            )

    def test_failed_requires_all_claims_removed(self) -> None:
        report = _repair_report(
            status="failed",
            kept_claim_ids=[],
            removed_claim_ids=["C1"],
            removal_reasons={"C1": ["verification_error"]},
        )
        assert report.kept_claim_ids == []

        with pytest.raises(ValidationError, match="移除全部"):
            _repair_report(
                status="failed",
                kept_claim_ids=["C1"],
                removed_claim_ids=[],
            )

    def test_removal_reasons_must_cover_removed_claims(self) -> None:
        with pytest.raises(ValidationError, match="removal_reasons"):
            _repair_report(
                status="failed",
                kept_claim_ids=[],
                removed_claim_ids=["C1"],
            )


class TestEvidenceQAAnswer:
    def test_answer_renders_only_verified_claims(self) -> None:
        claim = _claim()
        citation = _case_citation()
        answer = EvidenceQAAnswer(
            question="境外接收方有什么义务？",
            scope=EvidenceQAScope(
                owner_id="github:alice",
                corpora=["user_docs"],
            ),
            status="answered",
            claims=[claim],
            citations=[citation],
            verification=ClaimCitationVerifier.verify([claim], [citation]),
            support_verification=_support_result(),
            repair_report=_repair_report(),
        )
        assert answer.answer == "1. 境外接收方应承担安全保护责任。[E1]"
        assert "document_version_id" in answer.model_dump()["citations"][0]

    def test_partial_answer_marks_missing_evidence(self) -> None:
        claim = _claim()
        citation = _case_citation()
        answer = EvidenceQAAnswer(
            question="材料是否完整？",
            scope=EvidenceQAScope(
                owner_id="github:alice",
                corpora=["user_docs"],
            ),
            status="partially_answered",
            claims=[claim],
            citations=[citation],
            unanswered_aspects=["未找到保存期限"],
            verification=ClaimCitationVerifier.verify([claim], [citation]),
            support_verification=_support_result(),
            repair_report=_repair_report(),
        )
        assert answer.answer.startswith("⚠️")
        assert "未找到保存期限" in answer.answer

    def test_table_renders_only_verified_claim_text(self) -> None:
        claim = _claim()
        citation = _case_citation()
        answer = EvidenceQAAnswer(
            question="以表格说明义务",
            scope=EvidenceQAScope(owner_id="github:alice", corpora=["user_docs"]),
            status="answered",
            claims=[claim],
            citations=[citation],
            presentation=EvidenceQAPresentation(
                kind="table",
                table=EvidenceQATable(
                    title="义务对照",
                    rows=[EvidenceQATableRow(label="境外接收方", claim_ids=["C1"])],
                ),
            ),
            verification=ClaimCitationVerifier.verify([claim], [citation]),
            support_verification=_support_result(),
            repair_report=_repair_report(),
        )
        assert "| 项目 | 经核验结论 | 证据 |" in answer.answer
        assert "境外接收方应承担安全保护责任。" in answer.answer
        assert "| [E1] |" in answer.answer

    def test_flowchart_renders_server_generated_dsl(self) -> None:
        first = _claim(text="提交申报材料。")
        second = _claim(claim_id="C2", text="提交申报材料后进入完备性查验。")
        citation = _case_citation()
        support = ClaimSupportResult(
            judgements=[
                ClaimSupportJudgement(
                    claim_id=claim.claim_id,
                    supported=True,
                    citation_ids=["E1"],
                )
                for claim in (first, second)
            ],
            unsupported_claim_ids=[],
            valid=True,
        )
        answer = EvidenceQAAnswer(
            question="申报流程是什么？",
            scope=EvidenceQAScope(owner_id="github:alice", corpora=["user_docs"]),
            status="answered",
            claims=[first, second],
            citations=[citation],
            presentation=EvidenceQAPresentation(
                kind="flowchart",
                flowchart=EvidenceQAFlowchart(
                    title="申报流程",
                    nodes=[
                        EvidenceQAFlowNode(node_id="N1", claim_ids=["C1"]),
                        EvidenceQAFlowNode(node_id="N2", claim_ids=["C2"]),
                    ],
                    edges=[
                        EvidenceQAFlowEdge(
                            source_node_id="N1",
                            target_node_id="N2",
                            claim_ids=["C2"],
                        )
                    ],
                ),
            ),
            verification=ClaimCitationVerifier.verify([first, second], [citation]),
            support_verification=support,
            repair_report=_repair_report(kept_claim_ids=["C1", "C2"]),
        )
        assert "```riskpilot-flow" in answer.answer
        assert '"coverage":"complete"' in answer.answer
        assert '"from":"N1","to":"N2"' in answer.answer
        assert '"label":"进入下一步"' in answer.answer
        assert "流程依据：" in answer.answer

    def test_non_refusal_rejects_invalid_verification(self) -> None:
        claim = _claim(citation_ids=[])
        citation = _case_citation()
        with pytest.raises(ValidationError, match="必须通过"):
            EvidenceQAAnswer(
                question="问题",
                scope=EvidenceQAScope(owner_id="github:alice", corpora=["regulatory"]),
                status="answered",
                claims=[claim],
                citations=[citation],
                verification=ClaimCitationVerifier.verify([claim], [citation]),
                support_verification=_support_result(),
                repair_report=_repair_report(),
            )

    def test_refusal_has_no_citations_or_claims(self) -> None:
        verification = ClaimCitationVerifier.verify([], [])
        answer = EvidenceQAAnswer(
            question="未知问题",
            scope=EvidenceQAScope(owner_id="github:alice", corpora=["regulatory"]),
            status="refused",
            refusal_reason="根据当前检索范围未找到足够证据。",
            verification=verification,
            support_verification=ClaimSupportResult(
                judgements=[],
                unsupported_claim_ids=[],
                valid=True,
            ),
            repair_report=ClaimRepairReport(
                status="not_needed",
                original_claim_count=0,
            ),
        )
        assert answer.answer == "根据当前检索范围未找到足够证据。"
        assert answer.citations == []

    def test_verification_cannot_be_forged(self) -> None:
        claim = _claim()
        citation = _case_citation()
        forged = ClaimCitationVerifier.verify([], [])
        with pytest.raises(ValidationError, match="verification"):
            EvidenceQAAnswer(
                question="问题",
                scope=EvidenceQAScope(owner_id="github:alice", corpora=["user_docs"]),
                status="answered",
                claims=[claim],
                citations=[citation],
                verification=forged,
                support_verification=_support_result(),
                repair_report=_repair_report(),
            )

    def test_support_verification_must_cover_every_claim(self) -> None:
        claim = _claim()
        citation = _case_citation()
        with pytest.raises(ValidationError, match="覆盖全部"):
            EvidenceQAAnswer(
                question="问题",
                scope=EvidenceQAScope(
                    owner_id="github:alice",
                    corpora=["user_docs"],
                ),
                status="answered",
                claims=[claim],
                citations=[citation],
                verification=ClaimCitationVerifier.verify([claim], [citation]),
                support_verification=ClaimSupportResult(
                    judgements=[],
                    unsupported_claim_ids=[],
                    valid=True,
                ),
                repair_report=_repair_report(),
            )

    def test_unsupported_claim_blocks_answer(self) -> None:
        claim = _claim()
        citation = _case_citation()
        with pytest.raises(ValidationError, match="语义支持"):
            EvidenceQAAnswer(
                question="问题",
                scope=EvidenceQAScope(
                    owner_id="github:alice",
                    corpora=["user_docs"],
                ),
                status="answered",
                claims=[claim],
                citations=[citation],
                verification=ClaimCitationVerifier.verify([claim], [citation]),
                support_verification=_support_result(supported=False),
                repair_report=_repair_report(),
            )

    def test_repaired_answer_must_be_partial_and_match_kept_claims(self) -> None:
        claim = _claim()
        citation = _case_citation()
        report = _repair_report(
            status="repaired",
            kept_claim_ids=["C1"],
            removed_claim_ids=["C2"],
            removal_reasons={"C2": ["unsupported"]},
        )
        answer = EvidenceQAAnswer(
            question="问题",
            scope=EvidenceQAScope(
                owner_id="github:alice",
                corpora=["user_docs"],
            ),
            status="partially_answered",
            claims=[claim],
            citations=[citation],
            unanswered_aspects=["C2 未通过校验"],
            verification=ClaimCitationVerifier.verify([claim], [citation]),
            support_verification=_support_result(),
            repair_report=report,
        )
        assert answer.repair_report.removed_claim_ids == ["C2"]

        with pytest.raises(ValidationError, match="partially_answered"):
            EvidenceQAAnswer(
                question="问题",
                scope=EvidenceQAScope(
                    owner_id="github:alice",
                    corpora=["user_docs"],
                ),
                status="answered",
                claims=[claim],
                citations=[citation],
                verification=ClaimCitationVerifier.verify([claim], [citation]),
                support_verification=_support_result(),
                repair_report=report,
            )
