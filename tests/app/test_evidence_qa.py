"""V3 EvidenceQAUseCase 多范围检索、引用校验和安全拒答测试。"""

from __future__ import annotations

import hashlib
from datetime import date

import pytest

from app.use_cases import (
    AssessmentManagementUseCase,
    CaseManagementUseCase,
    PolicyManagementUseCase,
    WorkspaceManagementUseCase,
)
from app.use_cases.evidence_qa import EvidenceQAUseCase
from domain import (
    ActionItem,
    Assessment,
    AssessmentBundle,
    Case,
    CaseDocument,
    CaseNotFound,
    ClaimSupportJudgement,
    ClaimSupportResult,
    Document,
    DocumentParseSnapshot,
    DocumentVersion,
    EvidenceChunk,
    EvidenceQAClaim,
    EvidenceQADraft,
    Finding,
    ParsedPage,
    ProcessingJob,
)
from domain.models import Chunk
from tests.fakes import (
    FakeClaimSupportVerifier,
    FakeEmbed,
    FakeEvidenceIndex,
    FakeEvidenceQAGenerator,
    FakeRetrieve,
    InMemoryAssessmentRepo,
    InMemoryCaseFactRepo,
    InMemoryCaseRepo,
    InMemoryDocumentRepo,
    InMemoryPolicyRuleRepo,
    InMemoryWorkspaceRepo,
)


class _Setup:
    def __init__(self) -> None:
        self.workspace_repo = InMemoryWorkspaceRepo()
        self.case_repo = InMemoryCaseRepo()
        self.document_repo = InMemoryDocumentRepo()
        self.fact_repo = InMemoryCaseFactRepo()
        self.rule_repo = InMemoryPolicyRuleRepo()
        self.assessment_repo = InMemoryAssessmentRepo(self.case_repo)
        self.workspace_uc = WorkspaceManagementUseCase(self.workspace_repo)
        self.case_uc = CaseManagementUseCase(
            case_repo=self.case_repo,
            workspace_repo=self.workspace_repo,
        )
        self.policy_uc = PolicyManagementUseCase(
            rule_repo=self.rule_repo,
            fact_repo=self.fact_repo,
            case_management=self.case_uc,
            workspace_management=self.workspace_uc,
        )
        self.assessment_uc = AssessmentManagementUseCase(
            assessment_repo=self.assessment_repo,
            fact_repo=self.fact_repo,
            case_management=self.case_uc,
            workspace_management=self.workspace_uc,
            policy_management=self.policy_uc,
        )
        workspace = self.workspace_uc.create_workspace(
            "github:alice",
            name="跨境合规组",
        )
        self.workspace_id = workspace.workspace_id
        self.workspace_uc.add_or_update_member(
            workspace.workspace_id,
            "github:alice",
            user_id="github:editor",
            role="editor",
        )
        case = self.case_uc.create_case(
            "github:editor",
            workspace_id=workspace.workspace_id,
            title="海外客服项目",
            assessment_date=date(2026, 8, 7),
        )
        self.case_id = case.case_id
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
            workspace_management=self.workspace_uc,
            case_management=self.case_uc,
            assessment_management=self.assessment_uc,
        )

    def seed_document(
        self,
        *,
        document_id: str,
        case_id: str | None = None,
        document_type: str = "case_material",
        status: str = "ready",
        text: str = "境外接收方应承担安全保护责任",
    ) -> EvidenceChunk:
        bound_case_id = case_id or self.case_id
        version_id = f"ver_{document_id}"
        sha256 = hashlib.sha256(text.encode()).hexdigest()
        document = Document(
            document_id=document_id,
            workspace_id=self.workspace_id,
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
        binding = CaseDocument(
            case_id=bound_case_id,
            document_id=document_id,
            added_by="github:editor",
            added_at=100.0,
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
        self.document_repo.create_upload(document, version, binding, job)
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
            workspace_id=self.workspace_id,
            case_id=bound_case_id,
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

    def seed_assessment(self) -> str:
        assessment = Assessment(
            assessment_id="assessment_001",
            case_id=self.case_id,
            version=1,
            status="review_required",
            assessment_date=date(2026, 8, 7),
            jurisdiction="CN",
            ruleset_version="synthetic-v1",
            risk_level="high",
            candidate_paths=["security_assessment"],
            created_at=100.0,
            updated_at=100.0,
        )
        finding = Finding(
            finding_id="finding_001",
            assessment_id=assessment.assessment_id,
            finding_type="rule_trigger",
            severity="high",
            title="重要数据规则已触发",
            description="需要申报数据出境安全评估",
            clause_ids=["clause_001"],
            rule_ids=["rule_001"],
        )
        action = ActionItem(
            action_id="action_001",
            assessment_id=assessment.assessment_id,
            title="提交安全评估材料",
            priority="high",
            related_finding_ids=[finding.finding_id],
        )
        case = self.case_repo.get(self.case_id)
        assert case is not None
        updated_case = case.model_copy(update={"active_assessment_id": assessment.assessment_id})
        self.assessment_repo.create_version(
            AssessmentBundle(
                assessment=assessment,
                findings=[finding],
                action_items=[action],
            ),
            None,
            updated_case,
        )
        return assessment.assessment_id


@pytest.fixture
def setup() -> _Setup:
    return _Setup()


class TestEvidenceQAAnswer:
    def test_case_scope_returns_versioned_verified_citation(self, setup: _Setup) -> None:
        chunk = setup.seed_document(document_id="case")

        result = setup.qa.answer(
            "github:editor",
            question="境外接收方有什么义务？",
            corpora=["case"],
            case_id=setup.case_id,
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

    def test_workspace_scope_only_uses_workspace_knowledge(self, setup: _Setup) -> None:
        workspace_chunk = setup.seed_document(
            document_id="workspace",
            document_type="workspace_knowledge",
            text="Workspace 制度要求完成审批",
        )
        setup.seed_document(
            document_id="case",
            document_type="case_material",
            text="普通案件材料不能进入 Workspace 范围",
        )

        result = setup.qa.answer(
            "github:editor",
            question="内部制度要求什么？",
            corpora=["workspace"],
            workspace_id=setup.workspace_id,
        )

        assert result.status == "answered"
        assert [citation.document_id for citation in result.citations] == [
            workspace_chunk.document_id
        ]
        assert setup.index.workspace_search_calls[0]["workspace_id"] == setup.workspace_id

    def test_assessment_scope_explains_finding(self, setup: _Setup) -> None:
        assessment_id = setup.seed_assessment()

        result = setup.qa.answer(
            "github:editor",
            question="为什么是高风险？",
            corpora=["assessment"],
            case_id=setup.case_id,
            assessment_id=assessment_id,
        )

        assert result.status == "answered"
        assert result.citations[0].corpus == "assessment"
        assert result.citations[0].assessment_id == assessment_id
        assert "重要数据规则已触发" in result.citations[0].quote

    def test_multi_scope_deduplicates_and_renumbers_citations(self, setup: _Setup) -> None:
        setup.seed_document(document_id="case")
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
            corpora=["regulatory", "case"],
            case_id=setup.case_id,
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
            corpora=["case"],
            case_id=setup.case_id,
        )
        assert result.status == "refused"
        assert setup.generator.calls == []
        assert setup.support.calls == []

    def test_tampered_index_quote_is_dropped_before_llm(self, setup: _Setup) -> None:
        chunk = setup.seed_document(document_id="case")
        tampered = chunk.model_copy(update={"text": "索引中被篡改的原文"})
        setup.index.replace_version_chunks(
            chunk.document_version_id,
            [tampered],
            [[1.0] * 4],
        )

        result = setup.qa.answer(
            "github:editor",
            question="境外接收方有什么义务？",
            corpora=["case"],
            case_id=setup.case_id,
        )

        assert result.status == "refused"
        assert setup.generator.calls == []

    def test_stale_document_version_is_dropped_before_llm(self, setup: _Setup) -> None:
        chunk = setup.seed_document(document_id="case")
        document = setup.document_repo.get(chunk.document_id)
        assert document is not None
        setup.document_repo.update_document(
            document.model_copy(update={"current_version_id": "ver_new"})
        )

        result = setup.qa.answer(
            "github:editor",
            question="境外接收方有什么义务？",
            corpora=["case"],
            case_id=setup.case_id,
        )

        assert result.status == "refused"
        assert setup.generator.calls == []

    def test_unbound_case_document_is_dropped_before_llm(self, setup: _Setup) -> None:
        chunk = setup.seed_document(document_id="case")
        setup.document_repo._bindings.pop((setup.case_id, chunk.document_id))

        result = setup.qa.answer(
            "github:editor",
            question="境外接收方有什么义务？",
            corpora=["case"],
            case_id=setup.case_id,
        )

        assert result.status == "refused"
        assert setup.generator.calls == []

    def test_generator_unknown_or_missing_citation_refuses(self, setup: _Setup) -> None:
        setup.seed_document(document_id="case")
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
                corpora=["case"],
                case_id=setup.case_id,
            )
            assert result.status == "refused"
            assert "Claim-Citation" in result.refusal_reason

    def test_support_verifier_rejection_refuses(self, setup: _Setup) -> None:
        setup.seed_document(document_id="case")
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
            corpora=["case"],
            case_id=setup.case_id,
        )
        assert result.status == "refused"
        assert "Claim-Citation" in result.refusal_reason

    @pytest.mark.parametrize(
        "error",
        [ValueError("invalid json"), RuntimeError("provider unavailable")],
    )
    def test_generator_or_verifier_errors_refuse(
        self,
        setup: _Setup,
        error: Exception,
    ) -> None:
        setup.seed_document(document_id="case")
        generator = FakeEvidenceQAGenerator(error=error)
        result = setup.build_qa(generator=generator).answer(
            "github:editor",
            question="问题",
            corpora=["case"],
            case_id=setup.case_id,
        )
        assert result.status == "refused"
        assert "生成或校验失败" in result.refusal_reason


class TestEvidenceQAScopeIsolation:
    def test_outsider_cannot_discover_case(self, setup: _Setup) -> None:
        with pytest.raises(CaseNotFound):
            setup.qa.answer(
                "github:outsider",
                question="问题",
                corpora=["case"],
                case_id=setup.case_id,
            )

    def test_workspace_id_cannot_override_case_parent(self, setup: _Setup) -> None:
        with pytest.raises(ValueError, match="归属不一致"):
            setup.qa.answer(
                "github:editor",
                question="问题",
                corpora=["case"],
                workspace_id="ws_other",
                case_id=setup.case_id,
            )

    def test_assessment_must_belong_to_requested_case(self, setup: _Setup) -> None:
        assessment_id = setup.seed_assessment()
        other = Case(
            case_id="case_other",
            workspace_id=setup.workspace_id,
            title="other",
            owner_id="github:editor",
            created_at=100.0,
            updated_at=100.0,
        )
        setup.case_repo.create(other)
        with pytest.raises(ValueError, match="不属于"):
            setup.qa.answer(
                "github:editor",
                question="问题",
                corpora=["assessment"],
                case_id=other.case_id,
                assessment_id=assessment_id,
            )

    def test_duplicate_or_empty_scope_rejected(self, setup: _Setup) -> None:
        for corpora in ([], ["case", "case"]):
            with pytest.raises(ValueError):
                setup.qa.answer(
                    "github:editor",
                    question="问题",
                    corpora=corpora,  # type: ignore[arg-type]
                    case_id=setup.case_id,
                )
