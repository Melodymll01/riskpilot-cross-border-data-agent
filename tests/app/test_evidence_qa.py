"""EvidenceQAUseCase 多范围检索、引用校验和安全拒答测试。"""

from __future__ import annotations

import hashlib

import pytest

from app.use_cases.evidence_qa import EvidenceQAUseCase
from domain import (
    ClaimSupportJudgement,
    ClaimSupportResult,
    Document,
    DocumentParseSnapshot,
    DocumentVersion,
    EvidenceChunk,
    EvidenceQAClaim,
    EvidenceQADraft,
    EvidenceQAFlowchart,
    EvidenceQAFlowEdge,
    EvidenceQAFlowNode,
    EvidenceQAPresentation,
    EvidenceQAScope,
    EvidenceQATable,
    EvidenceQATableRow,
    EvidenceSearchHit,
    ParsedPage,
    ProcessingJob,
    UserContextMessage,
)
from domain.models import Chunk
from tests.fakes import (
    FakeClaimSupportVerifier,
    FakeEmbed,
    FakeEvidenceIndex,
    FakeEvidenceQAGenerator,
    FakeRetrieve,
    InMemoryDocumentRepo,
)


class _Setup:
    def __init__(self) -> None:
        self.document_repo = InMemoryDocumentRepo()
        self.owner_id = "github:editor"
        self.index = FakeEvidenceIndex(self.document_repo)
        self.embed = FakeEmbed(dim=4)
        self.retriever = FakeRetrieve()
        self.generator = FakeEvidenceQAGenerator()
        self.support = FakeClaimSupportVerifier()
        self.qa = self.build_qa()

    def build_qa(
        self,
        *,
        generator: FakeEvidenceQAGenerator | None = None,
        support: FakeClaimSupportVerifier | None = None,
        retriever: FakeRetrieve | None = None,
    ) -> EvidenceQAUseCase:
        return EvidenceQAUseCase(
            retriever=retriever or self.retriever,
            evidence_index=self.index,
            document_repo=self.document_repo,
            embedder=self.embed,
            generator=generator or self.generator,
            support_verifier=support or self.support,
        )

    def seed_document(
        self,
        *,
        document_id: str,
        document_type: str = "case_material",
        status: str = "ready",
        text: str = "境外接收方应承担安全保护责任",
    ) -> EvidenceChunk:
        version_id = f"ver_{document_id}"
        sha256 = hashlib.sha256(text.encode()).hexdigest()
        document = Document(
            document_id=document_id,
            owner_id=self.owner_id,
            logical_name=f"{document_id}.txt",
            document_type=document_type,
            status=status,  # type: ignore[arg-type]
            created_by="github:editor",
            current_version_id=version_id,
            created_at=100.0,
            updated_at=101.0,
        )
        version = DocumentVersion(
            version_id=version_id,
            document_id=document_id,
            version_number=1,
            object_key=f"objects/{document_id}.txt",
            sha256=sha256,
            mime_type="text/plain",
            size_bytes=len(text.encode()),
            parser_version="test",
            page_count=1,
            created_at=100.0,
        )
        job = ProcessingJob(
            job_id=f"job_{document_id}",
            document_version_id=version_id,
            status="completed" if status == "ready" else "queued",
            current_stage="ready" if status == "ready" else "validate",
            progress=1.0 if status == "ready" else 0.0,
            created_at=100.0,
            updated_at=101.0 if status == "ready" else 100.0,
            started_at=100.0 if status == "ready" else None,
            completed_at=101.0 if status == "ready" else None,
        )
        self.document_repo.create_upload(document, version, job)
        snapshot = DocumentParseSnapshot(
            snapshot_id=f"snapshot_{document_id}",
            document_version_id=version_id,
            parser_name="test",
            parser_version="test",
            source_sha256=sha256,
            pages=[
                ParsedPage(
                    page_number=1,
                    text=text,
                    extraction_method="native",
                )
            ],
            parsed_at=101.0,
        )
        self.document_repo._snapshots[version_id] = snapshot
        chunk = EvidenceChunk(
            chunk_id=f"chunk_{document_id}",
            owner_id=self.owner_id,
            document_id=document_id,
            document_version_id=version_id,
            page_number=1,
            chunk_index=0,
            text=text,
            source_sha256=sha256,
            created_at=101.0,
        )
        self.index.replace_version_chunks(version_id, [chunk], [[1.0] * 4])
        return chunk


@pytest.fixture
def setup() -> _Setup:
    return _Setup()


class TestEvidenceQAAnswer:
    def test_case_scope_returns_versioned_verified_citation(self, setup: _Setup) -> None:
        chunk = setup.seed_document(document_id="user_docs")

        result = setup.qa.answer(
            "github:editor",
            question="境外接收方有什么义务？",
            corpora=["user_docs"],
        )

        assert result.status == "answered"
        assert result.citations[0].document_version_id == chunk.document_version_id
        assert result.citations[0].page_number == 1
        assert result.citations[0].source_sha256 == chunk.source_sha256
        assert result.verification.valid is True
        assert result.support_verification.valid is True
        assert result.answer.endswith("[E1]")
        assert setup.generator.calls[0]["citations"] == result.citations

    def test_regulatory_scope_uses_public_corpus_only(self, setup: _Setup) -> None:
        retriever = FakeRetrieve(
            chunks=[
                Chunk(
                    chunk_id="law_001",
                    text="个人信息出境应具备法定条件之一。",
                    source_type="law",
                    source_name="个人信息保护法",
                    title="第三十八条",
                    score=0.9,
                )
            ]
        )
        qa = setup.build_qa(retriever=retriever)

        result = qa.answer(
            "github:editor",
            question="个人信息出境有什么条件？",
            corpora=["regulatory"],
        )

        assert result.status == "answered"
        call = retriever.calls[0]
        assert call["corpus"] == "law"
        assert call["owner_id"] is None
        assert result.citations[0].corpus == "regulatory"

    def test_multi_scope_deduplicates_and_renumbers_citations(self, setup: _Setup) -> None:
        setup.seed_document(document_id="user_docs")
        retriever = FakeRetrieve(
            chunks=[
                Chunk(
                    chunk_id="law_001",
                    text="法规证据",
                    source_type="law",
                    source_name="个人信息保护法",
                    score=0.9,
                )
            ]
        )
        generator = FakeEvidenceQAGenerator(
            EvidenceQADraft(
                status="answered",
                claims=[
                    EvidenceQAClaim(
                        claim_id="C1",
                        text="法规证据支持结论。",
                        citation_ids=["E1"],
                    )
                ],
            )
        )
        qa = setup.build_qa(generator=generator, retriever=retriever)

        result = qa.answer(
            "github:editor",
            question="综合说明",
            corpora=["regulatory", "user_docs"],
            top_k=2,
        )

        assert result.status == "answered"
        assert [citation.citation_id for citation in generator.calls[0]["citations"]] == [
            "E1",
            "E2",
        ]
        assert [citation.citation_id for citation in result.citations] == ["E1"]
        assert len(setup.embed.calls) == 1


class TestEvidenceQAFailClosed:
    def test_no_evidence_refuses_without_llm(self, setup: _Setup) -> None:
        result = setup.qa.answer(
            "github:editor",
            question="不存在的问题",
            corpora=["user_docs"],
        )
        assert result.status == "refused"
        assert setup.generator.calls == []
        assert setup.support.calls == []

    def test_tampered_index_quote_is_dropped_before_llm(self, setup: _Setup) -> None:
        chunk = setup.seed_document(document_id="user_docs")
        tampered = chunk.model_copy(update={"text": "索引中被篡改的原文"})
        setup.index.replace_version_chunks(
            chunk.document_version_id,
            [tampered],
            [[1.0] * 4],
        )

        result = setup.qa.answer(
            "github:editor",
            question="境外接收方有什么义务？",
            corpora=["user_docs"],
        )

        assert result.status == "refused"
        assert setup.generator.calls == []

    def test_stale_document_version_is_dropped_before_llm(self, setup: _Setup) -> None:
        chunk = setup.seed_document(document_id="user_docs")
        document = setup.document_repo.get(chunk.document_id)
        assert document is not None
        setup.document_repo.update_document(
            document.model_copy(update={"current_version_id": "ver_new"})
        )

        result = setup.qa.answer(
            "github:editor",
            question="境外接收方有什么义务？",
            corpora=["user_docs"],
        )

        assert result.status == "refused"
        assert setup.generator.calls == []

    def test_unbound_case_document_is_dropped_before_llm(self, setup: _Setup) -> None:
        chunk = setup.seed_document(document_id="user_docs")
        setup.document_repo._documents[chunk.document_id] = setup.document_repo.get(
            chunk.document_id
        ).model_copy(update={"owner_id": "github:outsider"})

        result = setup.qa.answer(
            "github:editor",
            question="境外接收方有什么义务？",
            corpora=["user_docs"],
        )

        assert result.status == "refused"
        assert setup.generator.calls == []

    @pytest.mark.parametrize(
        ("field_name", "foreign_value"),
        [
            ("owner_id", "ws_foreign"),
            ("document_id", "doc_foreign"),
        ],
    )
    def test_poisoned_index_cross_scope_chunk_is_dropped(
        self,
        setup: _Setup,
        field_name: str,
        foreign_value: str,
    ) -> None:
        chunk = setup.seed_document(document_id="user_docs")
        poisoned = chunk.model_copy(update={field_name: foreign_value})

        def poisoned_search(**_: object) -> list[EvidenceSearchHit]:
            return [
                EvidenceSearchHit(
                    chunk=poisoned,
                    score=0.99,
                    vector_score=0.99,
                    bm25_score=1.0,
                )
            ]

        setup.index.search = poisoned_search  # type: ignore[method-assign]

        result = setup.qa.answer(
            "github:editor",
            question="问题",
            corpora=["user_docs"],
        )

        assert result.status == "refused"
        assert setup.generator.calls == []

    def test_generator_unknown_or_missing_citation_refuses(self, setup: _Setup) -> None:
        setup.seed_document(document_id="user_docs")
        for citation_ids in ([], ["UNKNOWN"]):
            generator = FakeEvidenceQAGenerator(
                EvidenceQADraft(
                    status="answered",
                    claims=[
                        EvidenceQAClaim(
                            claim_id="C1",
                            text="不受支持的结论",
                            citation_ids=citation_ids,
                        )
                    ],
                )
            )
            result = setup.build_qa(generator=generator).answer(
                "github:editor",
                question="问题",
                corpora=["user_docs"],
            )
            assert result.status == "refused"
            assert "Claim-Citation" in result.refusal_reason

    def test_claim_with_unknown_user_condition_source_is_removed(self, setup: _Setup) -> None:
        setup.seed_document(document_id="user_docs")
        generator = FakeEvidenceQAGenerator(
            EvidenceQADraft(
                status="answered",
                claims=[
                    EvidenceQAClaim(
                        claim_id="C1",
                        text="用户人数满足该结论。",
                        citation_ids=["E1"],
                        condition_message_ids=["msg-invented"],
                    )
                ],
            )
        )
        qa = setup.build_qa(generator=generator)

        result = qa.answer_from_citations(
            question="如果人数翻倍呢？",
            scope=EvidenceQAScope(
                corpora=["user_docs"],
                owner_id=setup.owner_id,
                document_ids=["user_docs"],
            ),
            citations=setup.qa._retrieve(
                setup.owner_id,
                question="如果人数翻倍呢？",
                scope=EvidenceQAScope(
                    corpora=["user_docs"],
                    owner_id=setup.owner_id,
                    document_ids=["user_docs"],
                ),
                top_k=1,
            ),
            user_context=[UserContextMessage(message_id="msg-real", text="人数是2000。")],
        )

        assert result.status == "refused"
        assert result.repair_report.removal_reasons == {"C1": ["unknown_context"]}
        assert setup.support.calls == []

    def test_support_verifier_rejection_refuses(self, setup: _Setup) -> None:
        setup.seed_document(document_id="user_docs")
        support = FakeClaimSupportVerifier(
            ClaimSupportResult(
                judgements=[
                    ClaimSupportJudgement(
                        claim_id="C1",
                        supported=False,
                        citation_ids=[],
                        reason="证据未明确支持",
                    )
                ],
                unsupported_claim_ids=["C1"],
                valid=False,
            )
        )
        result = setup.build_qa(support=support).answer(
            "github:editor",
            question="问题",
            corpora=["user_docs"],
        )
        assert result.status == "refused"
        assert "Claim-Citation" in result.refusal_reason

    def test_mixed_claims_are_boundedly_repaired_to_partial_answer(
        self,
        setup: _Setup,
    ) -> None:
        setup.seed_document(document_id="user_docs")
        generator = FakeEvidenceQAGenerator(
            EvidenceQADraft(
                status="answered",
                claims=[
                    EvidenceQAClaim(
                        claim_id="C1",
                        text="境外接收方应承担安全保护责任。",
                        citation_ids=["E1"],
                    ),
                    EvidenceQAClaim(
                        claim_id="C2",
                        text="没有引用的结论。",
                        citation_ids=[],
                    ),
                    EvidenceQAClaim(
                        claim_id="C3",
                        text="原文不支持的结论。",
                        citation_ids=["E1"],
                    ),
                ],
                presentation=EvidenceQAPresentation(
                    kind="table",
                    table=EvidenceQATable(
                        rows=[
                            EvidenceQATableRow(label="可信", claim_ids=["C1"]),
                            EvidenceQATableRow(label="缺少引用", claim_ids=["C2"]),
                            EvidenceQATableRow(label="不受支持", claim_ids=["C3"]),
                        ]
                    ),
                ),
            )
        )
        support = FakeClaimSupportVerifier(
            ClaimSupportResult(
                judgements=[
                    ClaimSupportJudgement(
                        claim_id="C1",
                        supported=True,
                        citation_ids=["E1"],
                    ),
                    ClaimSupportJudgement(
                        claim_id="C3",
                        supported=False,
                        citation_ids=[],
                        reason="原文不支持",
                    ),
                ],
                unsupported_claim_ids=["C3"],
                valid=False,
            )
        )

        result = setup.build_qa(generator=generator, support=support).answer(
            "github:editor",
            question="综合判断",
            corpora=["user_docs"],
        )

        assert result.status == "partially_answered"
        assert [claim.claim_id for claim in result.claims] == ["C1"]
        assert [citation.citation_id for citation in result.citations] == ["E1"]
        assert result.support_verification.valid is True
        assert result.repair_report.status == "repaired"
        assert result.repair_report.removed_claim_ids == ["C2", "C3"]
        assert result.repair_report.removal_reasons == {
            "C2": ["uncited"],
            "C3": ["unsupported"],
        }
        assert len(generator.calls) == 1
        assert len(support.calls) == 1
        assert [claim.claim_id for claim in support.calls[0]["claims"]] == [
            "C1",
            "C3",
        ]
        assert result.presentation is not None
        assert result.presentation.kind == "table"
        assert [row.label for row in result.presentation.table.rows] == ["可信"]

    def test_bad_claim_removal_revalidates_generic_flow_and_degrades_to_text(
        self,
        setup: _Setup,
    ) -> None:
        setup.seed_document(document_id="user_docs")
        claims = [
            EvidenceQAClaim(
                claim_id="C1",
                text="系统收到事件后进入分类步骤。",
                citation_ids=["E1"],
            ),
            EvidenceQAClaim(
                claim_id="C2",
                text="系统根据事件级别选择上报路径。",
                citation_ids=["E1"],
            ),
            EvidenceQAClaim(
                claim_id="C3",
                text="事件为高优先级时，立即上报值班负责人。",
                citation_ids=["E1"],
            ),
            EvidenceQAClaim(
                claim_id="C4",
                text="事件为普通优先级时，进入常规处理队列。",
                citation_ids=["E1"],
            ),
        ]
        generator = FakeEvidenceQAGenerator(
            EvidenceQADraft(
                status="answered",
                claims=claims,
                presentation=EvidenceQAPresentation(
                    kind="flowchart",
                    flowchart=EvidenceQAFlowchart(
                        nodes=[
                            EvidenceQAFlowNode(node_id="N1", kind="start", claim_ids=["C1"]),
                            EvidenceQAFlowNode(node_id="N2", kind="decision", claim_ids=["C2"]),
                            EvidenceQAFlowNode(node_id="N3", kind="terminal", claim_ids=["C3"]),
                            EvidenceQAFlowNode(node_id="N4", kind="terminal", claim_ids=["C4"]),
                        ],
                        edges=[
                            EvidenceQAFlowEdge(
                                source_node_id="N1",
                                target_node_id="N2",
                                claim_ids=["C2"],
                            ),
                            EvidenceQAFlowEdge(
                                source_node_id="N2",
                                target_node_id="N3",
                                condition="高优先级",
                                claim_ids=["C3"],
                            ),
                            EvidenceQAFlowEdge(
                                source_node_id="N2",
                                target_node_id="N4",
                                condition="普通优先级",
                                claim_ids=["C4"],
                            ),
                        ],
                    ),
                ),
            )
        )
        support = FakeClaimSupportVerifier(
            ClaimSupportResult(
                judgements=[
                    ClaimSupportJudgement(
                        claim_id=claim.claim_id,
                        supported=claim.claim_id != "C4",
                        citation_ids=["E1"] if claim.claim_id != "C4" else [],
                        reason="" if claim.claim_id != "C4" else "原文不支持普通优先级分支",
                    )
                    for claim in claims
                ],
                unsupported_claim_ids=["C4"],
                valid=False,
            )
        )

        result = setup.build_qa(generator=generator, support=support).answer(
            "github:editor",
            question="事件如何上报？",
            corpora=["user_docs"],
        )

        assert result.status == "partially_answered"
        assert [claim.claim_id for claim in result.claims] == ["C1", "C2", "C3"]
        assert result.repair_report.removed_claim_ids == ["C4"]
        assert result.presentation == EvidenceQAPresentation(kind="text")

    def test_unknown_citation_is_removed_when_another_claim_is_safe(
        self,
        setup: _Setup,
    ) -> None:
        setup.seed_document(document_id="user_docs")
        generator = FakeEvidenceQAGenerator(
            EvidenceQADraft(
                status="answered",
                claims=[
                    EvidenceQAClaim(
                        claim_id="C1",
                        text="可信结论。",
                        citation_ids=["E1"],
                    ),
                    EvidenceQAClaim(
                        claim_id="C2",
                        text="伪造引用结论。",
                        citation_ids=["UNKNOWN"],
                    ),
                ],
            )
        )
        support = FakeClaimSupportVerifier()

        result = setup.build_qa(generator=generator, support=support).answer(
            "github:editor",
            question="问题",
            corpora=["user_docs"],
        )

        assert result.status == "partially_answered"
        assert result.repair_report.removal_reasons == {
            "C2": ["unknown_citation"],
        }
        assert [claim.claim_id for claim in support.calls[0]["claims"]] == ["C1"]

    def test_verifier_contract_error_refuses_all_claims(self, setup: _Setup) -> None:
        setup.seed_document(document_id="user_docs")
        support = FakeClaimSupportVerifier(
            ClaimSupportResult(
                judgements=[],
                unsupported_claim_ids=[],
                valid=True,
            )
        )

        result = setup.build_qa(support=support).answer(
            "github:editor",
            question="问题",
            corpora=["user_docs"],
        )

        assert result.status == "refused"
        assert result.repair_report.status == "failed"
        assert result.repair_report.removal_reasons == {
            "C1": ["verification_error"],
        }
        assert "生成或校验失败" in result.refusal_reason

    def test_unmodified_safe_answer_reports_no_repair(self, setup: _Setup) -> None:
        setup.seed_document(document_id="user_docs")

        result = setup.qa.answer(
            "github:editor",
            question="问题",
            corpora=["user_docs"],
        )

        assert result.status == "answered"
        assert result.repair_report.status == "not_needed"
        assert result.repair_report.kept_claim_ids == ["C1"]
        assert result.repair_report.removed_claim_ids == []

    @pytest.mark.parametrize(
        "error",
        [ValueError("invalid json"), RuntimeError("provider unavailable")],
    )
    def test_generator_or_verifier_errors_refuse(
        self,
        setup: _Setup,
        error: Exception,
    ) -> None:
        setup.seed_document(document_id="user_docs")
        generator = FakeEvidenceQAGenerator(error=error)
        result = setup.build_qa(generator=generator).answer(
            "github:editor",
            question="问题",
            corpora=["user_docs"],
        )
        assert result.status == "refused"
        assert "生成或校验失败" in result.refusal_reason


class TestEvidenceQAScopeIsolation:
    def test_outsider_cannot_discover_document(self, setup: _Setup) -> None:
        setup.seed_document(document_id="private")
        with pytest.raises(ValueError):
            setup.qa.answer(
                "github:outsider", question="问题", corpora=["user_docs"], document_ids=["private"]
            )

    def test_owner_cannot_be_overridden(self, setup: _Setup) -> None:
        with pytest.raises(TypeError):
            setup.qa.answer(
                "github:editor", question="问题", corpora=["user_docs"], owner_id="github:other"
            )

    def test_duplicate_or_empty_scope_rejected(self, setup: _Setup) -> None:
        for corpora in ([], ["user_docs", "user_docs"]):
            with pytest.raises(ValueError):
                setup.qa.answer(
                    "github:editor",
                    question="问题",
                    corpora=corpora,  # type: ignore[arg-type]
                )
