"""FactManagementUseCase 原文核验与确认权限测试。"""

from __future__ import annotations

import pytest

from app.use_cases import (
    CaseManagementUseCase,
    FactEvidenceInput,
    FactManagementUseCase,
    WorkspaceManagementUseCase,
)
from domain import (
    CaseDocument,
    Document,
    DocumentParseSnapshot,
    DocumentVersion,
    FactProposal,
    FactProposalEvidence,
    InvalidDocumentContent,
    ParsedPage,
    ProcessingJob,
    WorkspaceAccessDenied,
)
from tests.fakes import (
    InMemoryCaseFactRepo,
    InMemoryCaseRepo,
    InMemoryDocumentRepo,
    InMemoryWorkspaceRepo,
)
from tests.fakes.fake_fact_proposals import FakeFactProposalGenerator


def _setup(
    proposal_generator: FakeFactProposalGenerator | None = None,
):
    workspace_repo = InMemoryWorkspaceRepo()
    case_repo = InMemoryCaseRepo()
    document_repo = InMemoryDocumentRepo()
    fact_repo = InMemoryCaseFactRepo()
    workspace_uc = WorkspaceManagementUseCase(workspace_repo)
    case_uc = CaseManagementUseCase(
        case_repo=case_repo,
        workspace_repo=workspace_repo,
    )
    use_case = FactManagementUseCase(
        fact_repo=fact_repo,
        document_repo=document_repo,
        proposal_generator=proposal_generator,
        case_management=case_uc,
        workspace_management=workspace_uc,
    )
    workspace = workspace_uc.create_workspace("github:alice", name="跨境合规组")
    case = case_uc.create_case(
        "github:alice",
        workspace_id=workspace.workspace_id,
        title="案件",
    )
    workspace_uc.add_or_update_member(
        workspace.workspace_id,
        "github:alice",
        user_id="github:editor",
        role="editor",
    )
    workspace_uc.add_or_update_member(
        workspace.workspace_id,
        "github:alice",
        user_id="github:reviewer",
        role="reviewer",
    )
    version = DocumentVersion(
        version_id="ver_001",
        document_id="doc_001",
        version_number=1,
        object_key="ws/doc/ver/source.txt",
        sha256="a" * 64,
        mime_type="text/plain",
        size_bytes=20,
        parser_version="test",
        page_count=1,
        created_at=100.0,
    )
    document = Document(
        document_id="doc_001",
        workspace_id=workspace.workspace_id,
        logical_name="source.txt",
        document_type="case_material",
        status="ready",
        created_by="github:alice",
        current_version_id=version.version_id,
        created_at=100.0,
        updated_at=100.0,
    )
    document_repo.create_upload(
        document,
        version,
        CaseDocument(
            case_id=case.case_id,
            document_id=document.document_id,
            added_by="github:alice",
            added_at=100.0,
        ),
        ProcessingJob(
            job_id="job_001",
            document_version_id=version.version_id,
            created_at=100.0,
            updated_at=100.0,
        ),
    )
    document_repo._snapshots[version.version_id] = DocumentParseSnapshot(
        snapshot_id="parse_001",
        document_version_id=version.version_id,
        parser_name="fake",
        parser_version="test",
        source_sha256=version.sha256,
        pages=[
            ParsedPage(
                page_number=1,
                text="材料明确说明涉及重要数据，并由境外接收方承担责任。",
                extraction_method="native",
            )
        ],
        parsed_at=101.0,
    )
    return use_case, fact_repo, case.case_id


def _evidence_input(**overrides: object) -> FactEvidenceInput:
    values: dict[str, object] = {
        "document_id": "doc_001",
        "document_version_id": "ver_001",
        "page_number": 1,
        "quote": "涉及重要数据",
        "confidence": 0.95,
    }
    values.update(overrides)
    return FactEvidenceInput(**values)  # type: ignore[arg-type]


class TestFactCreation:
    def test_document_fact_requires_verified_evidence(self) -> None:
        use_case, repo, case_id = _setup()
        detail = use_case.create_fact(
            "github:editor",
            case_id=case_id,
            field_name="important_data_involved",
            value=True,
            source_type="document",
            confidence=0.95,
            criticality="critical",
            evidence=[_evidence_input()],
        )
        assert detail.fact.status == "proposed"
        assert detail.evidence[0].quote == "涉及重要数据"
        assert repo.get(detail.fact.fact_id) == detail.fact

    def test_document_fact_without_evidence_rejected(self) -> None:
        use_case, _, case_id = _setup()
        with pytest.raises(InvalidDocumentContent, match="必须包含"):
            use_case.create_fact(
                "github:editor",
                case_id=case_id,
                field_name="important_data_involved",
                value=True,
                source_type="document",
                confidence=0.8,
            )

    def test_quote_not_found_rejected(self) -> None:
        use_case, _, case_id = _setup()
        with pytest.raises(InvalidDocumentContent, match="未在"):
            use_case.create_fact(
                "github:editor",
                case_id=case_id,
                field_name="important_data_involved",
                value=True,
                source_type="document",
                confidence=0.8,
                evidence=[_evidence_input(quote="不存在的原文")],
            )

    def test_offset_must_match_quote(self) -> None:
        use_case, _, case_id = _setup()
        with pytest.raises(InvalidDocumentContent, match="offset"):
            use_case.create_fact(
                "github:editor",
                case_id=case_id,
                field_name="important_data_involved",
                value=True,
                source_type="document",
                confidence=0.8,
                evidence=[
                    _evidence_input(
                        start_offset=0,
                        end_offset=6,
                    )
                ],
            )

    def test_user_fact_can_have_no_document_evidence(self) -> None:
        use_case, _, case_id = _setup()
        detail = use_case.create_fact(
            "github:editor",
            case_id=case_id,
            field_name="destination_country",
            value="DE",
            source_type="user",
            confidence=1.0,
        )
        assert detail.evidence == []


class TestFactReviewAndRevision:
    def test_critical_fact_requires_reviewer_to_confirm(self) -> None:
        use_case, _, case_id = _setup()
        detail = use_case.create_fact(
            "github:editor",
            case_id=case_id,
            field_name="important_data_involved",
            value=True,
            source_type="document",
            confidence=0.95,
            criticality="critical",
            evidence=[_evidence_input()],
        )
        with pytest.raises(WorkspaceAccessDenied):
            use_case.transition_fact(
                detail.fact.fact_id,
                "github:editor",
                "confirmed",
            )
        confirmed = use_case.transition_fact(
            detail.fact.fact_id,
            "github:reviewer",
            "confirmed",
        )
        assert confirmed.confirmed_by == "github:reviewer"

    def test_normal_fact_editor_can_confirm(self) -> None:
        use_case, _, case_id = _setup()
        detail = use_case.create_fact(
            "github:editor",
            case_id=case_id,
            field_name="destination_country",
            value="DE",
            source_type="user",
            confidence=1.0,
        )
        confirmed = use_case.transition_fact(
            detail.fact.fact_id,
            "github:editor",
            "confirmed",
        )
        assert confirmed.status == "confirmed"

    def test_editor_cannot_replace_existing_same_field_fact(self) -> None:
        use_case, _, case_id = _setup()
        use_case.create_fact(
            "github:editor",
            case_id=case_id,
            field_name="destination_country",
            value="DE",
            source_type="user",
            confidence=1.0,
        )
        replacement = use_case.create_fact(
            "github:editor",
            case_id=case_id,
            field_name="destination_country",
            value="SG",
            source_type="user",
            confidence=1.0,
        )

        with pytest.raises(WorkspaceAccessDenied):
            use_case.transition_fact(
                replacement.fact.fact_id,
                "github:editor",
                "confirmed",
            )

    def test_revision_preserves_old_version_and_rebinds_evidence(self) -> None:
        use_case, repo, case_id = _setup()
        detail = use_case.create_fact(
            "github:editor",
            case_id=case_id,
            field_name="important_data_involved",
            value=True,
            source_type="document",
            confidence=0.95,
            evidence=[_evidence_input()],
        )
        revised = use_case.revise_fact(
            detail.fact.fact_id,
            "github:editor",
            value=False,
            source_type="document",
            confidence=0.7,
            evidence=[_evidence_input(quote="境外接收方承担责任")],
        )
        assert revised.fact.version == 2
        assert repo.get_version(detail.fact.fact_id, 1) == detail.fact
        assert revised.evidence[0].fact_version == 2


def _proposal(
    *,
    field_name: str = "important_data_involved",
    value: object = True,
    document_id: str = "doc_001",
    document_version_id: str = "ver_001",
    quote: str = "涉及重要数据",
) -> FactProposal:
    return FactProposal(
        field_name=field_name,
        value=value,
        confidence=0.95,
        evidence=[
            FactProposalEvidence(
                document_id=document_id,
                document_version_id=document_version_id,
                page_number=1,
                quote=quote,
                confidence=0.95,
            )
        ],
    )


class TestFactProposal:
    def test_document_proposal_is_critical_and_requires_reviewer(self) -> None:
        generator = FakeFactProposalGenerator([_proposal()])
        use_case, _, case_id = _setup(generator)

        batch = use_case.propose_from_documents(
            "github:editor",
            case_id=case_id,
            field_names=["important_data_involved"],
        )

        assert len(batch.facts) == 1
        detail = batch.facts[0]
        assert detail.fact.status == "proposed"
        assert detail.fact.criticality == "critical"
        assert detail.fact.source_type == "document"
        assert detail.fact.usable_for_rules is False
        assert batch.source_document_ids == ["doc_001"]
        assert batch.conflict_field_names == []
        with pytest.raises(WorkspaceAccessDenied):
            use_case.transition_fact(
                detail.fact.fact_id,
                "github:editor",
                "confirmed",
            )
        confirmed = use_case.transition_fact(
            detail.fact.fact_id,
            "github:reviewer",
            "confirmed",
        )
        assert confirmed.usable_for_rules is True

    def test_different_value_for_existing_field_is_conflicting(self) -> None:
        generator = FakeFactProposalGenerator([_proposal(value=False)])
        use_case, _, case_id = _setup(generator)
        use_case.create_fact(
            "github:editor",
            case_id=case_id,
            field_name="important_data_involved",
            value=True,
            source_type="user",
            confidence=1.0,
        )

        batch = use_case.propose_from_documents(
            "github:editor",
            case_id=case_id,
            field_names=["important_data_involved"],
        )

        assert batch.facts[0].fact.status == "conflicting"
        assert batch.conflict_field_names == ["important_data_involved"]
        confirmed = use_case.transition_fact(
            batch.facts[0].fact.fact_id,
            "github:reviewer",
            "confirmed",
        )
        assert confirmed.status == "confirmed"
        facts = use_case.list_facts(case_id, "github:reviewer")
        by_id = {fact.fact_id: fact for fact in facts}
        assert by_id[confirmed.fact_id].status == "confirmed"
        existing = next(fact for fact in facts if fact.fact_id != confirmed.fact_id)
        assert existing.status == "rejected"
        assert (
            len(
                [
                    fact
                    for fact in facts
                    if fact.field_name == "important_data_involved" and fact.status == "confirmed"
                ]
            )
            == 1
        )

    def test_conflict_detection_is_json_type_sensitive(self) -> None:
        generator = FakeFactProposalGenerator([_proposal(value=1)])
        use_case, _, case_id = _setup(generator)
        use_case.create_fact(
            "github:editor",
            case_id=case_id,
            field_name="important_data_involved",
            value=True,
            source_type="user",
            confidence=1.0,
        )

        batch = use_case.propose_from_documents(
            "github:editor",
            case_id=case_id,
            field_names=["important_data_involved"],
        )

        assert batch.facts[0].fact.status == "conflicting"

    def test_reconfirm_existing_value_rejects_conflicting_candidate(self) -> None:
        generator = FakeFactProposalGenerator([_proposal(value=False)])
        use_case, _, case_id = _setup(generator)
        original = use_case.create_fact(
            "github:editor",
            case_id=case_id,
            field_name="important_data_involved",
            value=True,
            source_type="user",
            confidence=1.0,
            criticality="critical",
        )
        use_case.transition_fact(
            original.fact.fact_id,
            "github:reviewer",
            "confirmed",
        )
        batch = use_case.propose_from_documents(
            "github:editor",
            case_id=case_id,
            field_names=["important_data_involved"],
        )
        assert batch.facts[0].fact.status == "conflicting"

        kept = use_case.transition_fact(
            original.fact.fact_id,
            "github:reviewer",
            "confirmed",
        )

        assert kept.status == "confirmed"
        facts = use_case.list_facts(case_id, "github:reviewer")
        candidate = next(fact for fact in facts if fact.fact_id != kept.fact_id)
        assert candidate.status == "rejected"

    def test_port_output_outside_allowlist_or_document_scope_rejected(self) -> None:
        outside_field = FakeFactProposalGenerator([_proposal(field_name="invented_field")])
        use_case, repo, case_id = _setup(outside_field)
        with pytest.raises(ValueError, match="白名单"):
            use_case.propose_from_documents(
                "github:editor",
                case_id=case_id,
                field_names=["important_data_involved"],
            )
        assert repo.list_for_case(case_id) == []

        outside_document = FakeFactProposalGenerator(
            [_proposal(document_id="doc_other", document_version_id="ver_other")]
        )
        use_case, repo, case_id = _setup(outside_document)
        with pytest.raises(InvalidDocumentContent, match="范围外"):
            use_case.propose_from_documents(
                "github:editor",
                case_id=case_id,
                field_names=["important_data_involved"],
            )
        assert repo.list_for_case(case_id) == []

    def test_invalid_quote_rolls_back_whole_batch(self) -> None:
        generator = FakeFactProposalGenerator(
            [
                _proposal(),
                _proposal(
                    field_name="destination_country",
                    value="DE",
                    quote="不存在的原文",
                ),
            ]
        )
        use_case, repo, case_id = _setup(generator)

        with pytest.raises(InvalidDocumentContent, match="未在"):
            use_case.propose_from_documents(
                "github:editor",
                case_id=case_id,
                field_names=["important_data_involved", "destination_country"],
            )

        assert repo.list_for_case(case_id) == []

    def test_empty_proposal_batch_is_valid(self) -> None:
        generator = FakeFactProposalGenerator([])
        use_case, repo, case_id = _setup(generator)

        batch = use_case.propose_from_documents(
            "github:editor",
            case_id=case_id,
            field_names=["important_data_involved"],
            document_ids=["doc_001"],
        )

        assert batch.facts == []
        assert repo.list_for_case(case_id) == []
        assert generator.calls[0]["field_names"] == ["important_data_involved"]

    def test_proposal_request_limits_fields(self) -> None:
        generator = FakeFactProposalGenerator([])
        use_case, _, case_id = _setup(generator)

        with pytest.raises(ValueError, match="不能超过 20"):
            use_case.propose_from_documents(
                "github:editor",
                case_id=case_id,
                field_names=[f"field_{index}" for index in range(21)],
            )
        assert generator.calls == []
