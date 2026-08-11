"""AssessmentManagementUseCase 确定性生成与版本测试。"""

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
from domain import (
    CaseDocument,
    CaseFact,
    CaseFactEvidence,
    Document,
    DocumentParseSnapshot,
    DocumentVersion,
    ParsedPage,
    PolicyRule,
    ProcessingJob,
)
from domain.errors import (
    AssessmentNotActive,
    InvalidDocumentContent,
    WorkspaceAccessDenied,
)
from tests.fakes import (
    InMemoryAssessmentRepo,
    InMemoryCaseFactRepo,
    InMemoryCaseRepo,
    InMemoryDocumentRepo,
    InMemoryPolicyRuleRepo,
    InMemoryWorkspaceRepo,
)


def _setup():
    workspace_repo = InMemoryWorkspaceRepo()
    case_repo = InMemoryCaseRepo()
    fact_repo = InMemoryCaseFactRepo()
    document_repo = InMemoryDocumentRepo()
    rule_repo = InMemoryPolicyRuleRepo()
    assessment_repo = InMemoryAssessmentRepo(case_repo)
    workspace_uc = WorkspaceManagementUseCase(workspace_repo)
    case_uc = CaseManagementUseCase(
        case_repo=case_repo,
        workspace_repo=workspace_repo,
    )
    policy_uc = PolicyManagementUseCase(
        rule_repo=rule_repo,
        fact_repo=fact_repo,
        case_management=case_uc,
        workspace_management=workspace_uc,
    )
    assessment_uc = AssessmentManagementUseCase(
        assessment_repo=assessment_repo,
        fact_repo=fact_repo,
        document_repo=document_repo,
        case_management=case_uc,
        workspace_management=workspace_uc,
        policy_management=policy_uc,
    )
    workspace = workspace_uc.create_workspace("github:alice", name="跨境合规组")
    case = case_uc.create_case(
        "github:alice",
        workspace_id=workspace.workspace_id,
        title="案件",
        assessment_date=date(2026, 8, 6),
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
    case_uc.transition_case(case.case_id, "github:alice", "collecting")
    case_uc.transition_case(case.case_id, "github:alice", "ready_for_assessment")
    return (
        assessment_uc,
        policy_uc,
        fact_repo,
        case_repo,
        assessment_repo,
        workspace.workspace_id,
        case.case_id,
    )


def _confirmed_fact(case_id: str, field_name: str, value: object) -> CaseFact:
    return CaseFact(
        fact_id=f"fact_{field_name}",
        case_id=case_id,
        field_name=field_name,
        value=value,  # type: ignore[arg-type]
        status="confirmed",
        source_type="user",
        confidence=1.0,
        created_by="github:editor",
        confirmed_by="github:alice",
        confirmed_at=101.0,
        created_at=100.0,
        updated_at=101.0,
    )


def _confirmed_document_fact(
    assessment_uc: AssessmentManagementUseCase,
    fact_repo: InMemoryCaseFactRepo,
    case_id: str,
    *,
    field_name: str = "flag",
    value: object = True,
) -> tuple[CaseFact, CaseFactEvidence]:
    document_repo = assessment_uc._documents
    text = "材料明确说明 flag 为 true。"
    sha256 = hashlib.sha256(text.encode()).hexdigest()
    document_id = f"doc_{field_name}"
    version_id = f"ver_{field_name}"
    document_repo.create_upload(
        Document(
            document_id=document_id,
            workspace_id=assessment_uc._case_management.get_case(
                case_id, "github:editor"
            ).workspace_id,
            logical_name=f"{field_name}.txt",
            document_type="case_material",
            status="ready",
            created_by="github:editor",
            current_version_id=version_id,
            created_at=100.0,
            updated_at=101.0,
        ),
        DocumentVersion(
            version_id=version_id,
            document_id=document_id,
            version_number=1,
            object_key=f"objects/{field_name}.txt",
            sha256=sha256,
            mime_type="text/plain",
            size_bytes=len(text.encode()),
            parser_version="test",
            page_count=1,
            created_at=100.0,
        ),
        CaseDocument(
            case_id=case_id,
            document_id=document_id,
            added_by="github:editor",
            added_at=100.0,
        ),
        ProcessingJob(
            job_id=f"job_{field_name}",
            document_version_id=version_id,
            status="completed",
            current_stage="ready",
            progress=1.0,
            created_at=100.0,
            updated_at=101.0,
            started_at=100.0,
            completed_at=101.0,
        ),
    )
    document_repo._snapshots[version_id] = DocumentParseSnapshot(
        snapshot_id=f"snapshot_{field_name}",
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
    fact = _confirmed_fact(case_id, field_name, value).model_copy(
        update={"source_type": "document"}
    )
    evidence = CaseFactEvidence(
        evidence_id=f"evidence_{field_name}",
        case_id=case_id,
        fact_id=fact.fact_id,
        fact_version=fact.version,
        document_id=document_id,
        document_version_id=version_id,
        page_number=1,
        quote="flag 为 true",
        confidence=0.95,
        created_at=101.0,
    )
    fact_repo.create(fact, [evidence])
    return fact, evidence


def _rule(
    workspace_id: str,
    *,
    required_fact_fields: list[str],
    condition: dict,
    result: dict,
) -> PolicyRule:
    return PolicyRule(
        workspace_id=workspace_id,
        rule_id="SYNTHETIC-001",
        ruleset_version="synthetic-v1",
        jurisdiction="CN",
        effective_from=date(2026, 1, 1),
        status="published",
        required_fact_fields=required_fact_fields,
        condition=condition,
        result=result,
        source_clause_ids=["synthetic-clause"],
    )


def _publish_rule(
    policy_uc: PolicyManagementUseCase,
    workspace_id: str,
    rule: PolicyRule,
) -> None:
    created = policy_uc.create_rule(
        workspace_id,
        "github:alice",
        rule.model_copy(update={"status": "draft"}),
    )
    policy_uc.publish_rule(
        workspace_id,
        "github:alice",
        rule_id=created.rule_id,
        ruleset_version=created.ruleset_version,
    )


class TestAssessmentGeneration:
    def test_triggered_rule_generates_finding_action_and_snapshot(self) -> None:
        (
            assessment_uc,
            policy_uc,
            fact_repo,
            case_repo,
            _,
            workspace_id,
            case_id,
        ) = _setup()
        fact = _confirmed_fact(case_id, "flag", True)
        fact_repo.create(fact, [])
        _publish_rule(
            policy_uc,
            workspace_id,
            _rule(
                workspace_id,
                required_fact_fields=["flag"],
                condition={"field": "flag", "operator": "eq", "value": True},
                result={
                    "candidate_path": "synthetic",
                    "risk_level": "high",
                    "required_actions": ["执行合成检查"],
                    "required_materials": ["合成材料"],
                },
            ),
        )
        bundle = assessment_uc.generate(
            case_id,
            "github:editor",
            ruleset_version="synthetic-v1",
            generated_by_run_id="run_001",
        )
        assert bundle.assessment.status == "review_required"
        assert bundle.assessment.fact_versions == {"flag": 1}
        assert bundle.assessment.risk_level == "high"
        assert bundle.assessment.candidate_paths == ["synthetic"]
        assert bundle.assessment.generated_by_run_id == "run_001"
        assert bundle.findings[0].finding_type == "rule_trigger"
        assert bundle.findings[0].fact_ids == [fact.fact_id]
        assert {item.title for item in bundle.action_items} == {
            "执行合成检查",
            "补充材料：合成材料",
        }
        active_case = case_repo.get(case_id)
        assert active_case is not None
        assert active_case.status == "review_required"

    def test_document_fact_finding_freezes_verified_evidence(self) -> None:
        (
            assessment_uc,
            policy_uc,
            fact_repo,
            _,
            _,
            workspace_id,
            case_id,
        ) = _setup()
        fact, evidence = _confirmed_document_fact(
            assessment_uc,
            fact_repo,
            case_id,
        )
        _publish_rule(
            policy_uc,
            workspace_id,
            _rule(
                workspace_id,
                required_fact_fields=["flag"],
                condition={"field": "flag", "operator": "eq", "value": True},
                result={"risk_level": "high"},
            ),
        )

        bundle = assessment_uc.generate(
            case_id,
            "github:editor",
            ruleset_version="synthetic-v1",
        )

        assert bundle.findings[0].fact_ids == [fact.fact_id]
        assert len(bundle.findings[0].evidence_ids) == 1
        citation = bundle.evidence_citations[0]
        assert bundle.findings[0].evidence_ids == [citation.citation_id]
        assert citation.source_evidence_id == evidence.evidence_id
        assert citation.fact_version == fact.version
        assert citation.quote == evidence.quote
        assert len(citation.source_sha256) == 64

    def test_document_fact_drift_blocks_assessment_generation(self) -> None:
        (
            assessment_uc,
            policy_uc,
            fact_repo,
            _,
            _,
            workspace_id,
            case_id,
        ) = _setup()
        fact, _ = _confirmed_document_fact(
            assessment_uc,
            fact_repo,
            case_id,
        )
        _publish_rule(
            policy_uc,
            workspace_id,
            _rule(
                workspace_id,
                required_fact_fields=["flag"],
                condition={"field": "flag", "operator": "eq", "value": True},
                result={"risk_level": "high"},
            ),
        )
        document = assessment_uc._documents.get("doc_flag")
        assert document is not None
        assessment_uc._documents.update_document(
            document.model_copy(update={"current_version_id": "ver_new"})
        )

        with pytest.raises(InvalidDocumentContent, match="失效"):
            assessment_uc.generate(
                case_id,
                "github:editor",
                ruleset_version="synthetic-v1",
            )
        assert fact.status == "confirmed"

    def test_missing_fact_generates_gap_finding_and_action(self) -> None:
        (
            assessment_uc,
            policy_uc,
            _,
            _,
            _,
            workspace_id,
            case_id,
        ) = _setup()
        _publish_rule(
            policy_uc,
            workspace_id,
            _rule(
                workspace_id,
                required_fact_fields=["missing_field"],
                condition={
                    "field": "missing_field",
                    "operator": "eq",
                    "value": True,
                },
                result={"candidate_path": "synthetic"},
            ),
        )
        bundle = assessment_uc.generate(
            case_id,
            "github:editor",
            ruleset_version="synthetic-v1",
        )
        assert bundle.assessment.risk_level == "unknown"
        assert bundle.findings[0].finding_type == "missing_fact"
        assert bundle.action_items[0].title == "确认事实：missing_field"

    def test_second_version_supersedes_first_and_updates_active_case(self) -> None:
        (
            assessment_uc,
            policy_uc,
            fact_repo,
            case_repo,
            assessment_repo,
            workspace_id,
            case_id,
        ) = _setup()
        fact_repo.create(_confirmed_fact(case_id, "flag", True), [])
        _publish_rule(
            policy_uc,
            workspace_id,
            _rule(
                workspace_id,
                required_fact_fields=["flag"],
                condition={"field": "flag", "operator": "eq", "value": True},
                result={"risk_level": "medium"},
            ),
        )
        first = assessment_uc.generate(
            case_id,
            "github:editor",
            ruleset_version="synthetic-v1",
        )
        second = assessment_uc.generate(
            case_id,
            "github:editor",
            ruleset_version="synthetic-v1",
        )
        assert first.assessment.version == 1
        assert second.assessment.version == 2
        versions = assessment_repo.list_for_case(case_id)
        assert versions[1].status == "superseded"
        active_case = case_repo.get(case_id)
        assert active_case is not None
        assert active_case.active_assessment_id == second.assessment.assessment_id

    def test_duplicate_confirmed_field_rejected(self) -> None:
        (
            assessment_uc,
            policy_uc,
            fact_repo,
            _,
            _,
            workspace_id,
            case_id,
        ) = _setup()
        first = _confirmed_fact(case_id, "flag", True)
        second = first.model_copy(update={"fact_id": "fact_flag_2"})
        fact_repo.create(first, [])
        fact_repo.create(second, [])
        _publish_rule(
            policy_uc,
            workspace_id,
            _rule(
                workspace_id,
                required_fact_fields=["flag"],
                condition={"field": "flag", "operator": "eq", "value": True},
                result={"risk_level": "high"},
            ),
        )
        with pytest.raises(ValueError, match="多个 confirmed"):
            assessment_uc.generate(
                case_id,
                "github:editor",
                ruleset_version="synthetic-v1",
            )

    def test_invalid_rule_result_rejected(self) -> None:
        (
            assessment_uc,
            policy_uc,
            fact_repo,
            _,
            _,
            workspace_id,
            case_id,
        ) = _setup()
        fact_repo.create(_confirmed_fact(case_id, "flag", True), [])
        _publish_rule(
            policy_uc,
            workspace_id,
            _rule(
                workspace_id,
                required_fact_fields=["flag"],
                condition={"field": "flag", "operator": "eq", "value": True},
                result={"risk_level": "impossible"},
            ),
        )
        with pytest.raises(ValueError, match="risk_level"):
            assessment_uc.generate(
                case_id,
                "github:editor",
                ruleset_version="synthetic-v1",
            )

    def test_empty_ruleset_rejected(self) -> None:
        (
            assessment_uc,
            _,
            _,
            _,
            _,
            _,
            case_id,
        ) = _setup()

        with pytest.raises(ValueError, match="没有已发布规则"):
            assessment_uc.generate(
                case_id,
                "github:editor",
                ruleset_version="missing-v1",
            )

    def test_ruleset_without_effective_rule_rejected(self) -> None:
        (
            assessment_uc,
            policy_uc,
            _,
            _,
            _,
            workspace_id,
            case_id,
        ) = _setup()
        future_rule = _rule(
            workspace_id,
            required_fact_fields=["flag"],
            condition={"field": "flag", "operator": "eq", "value": True},
            result={},
        ).model_copy(update={"effective_from": date(2027, 1, 1)})
        _publish_rule(policy_uc, workspace_id, future_rule)

        with pytest.raises(ValueError, match="没有生效规则"):
            assessment_uc.generate(
                case_id,
                "github:editor",
                ruleset_version="synthetic-v1",
            )


class TestAssessmentReview:
    def test_reviewer_approves_active_assessment_and_completes_case(self) -> None:
        (
            assessment_uc,
            policy_uc,
            fact_repo,
            case_repo,
            _,
            workspace_id,
            case_id,
        ) = _setup()
        fact_repo.create(_confirmed_fact(case_id, "flag", True), [])
        _publish_rule(
            policy_uc,
            workspace_id,
            _rule(
                workspace_id,
                required_fact_fields=["flag"],
                condition={"field": "flag", "operator": "eq", "value": True},
                result={"risk_level": "high"},
            ),
        )
        generated = assessment_uc.generate(
            case_id,
            "github:editor",
            ruleset_version="synthetic-v1",
        )

        reviewed = assessment_uc.review(
            generated.assessment.assessment_id,
            "github:reviewer",
            decision="approved",
            comment="证据与规则核验通过",
        )

        assert reviewed.assessment.status == "approved"
        assert reviewed.assessment.approved_by == "github:reviewer"
        assert reviewed.assessment.review_comment == "证据与规则核验通过"
        case = case_repo.get(case_id)
        assert case is not None
        assert case.status == "completed"

    def test_document_evidence_drift_blocks_approval(self) -> None:
        (
            assessment_uc,
            policy_uc,
            fact_repo,
            case_repo,
            _,
            workspace_id,
            case_id,
        ) = _setup()
        _confirmed_document_fact(assessment_uc, fact_repo, case_id)
        _publish_rule(
            policy_uc,
            workspace_id,
            _rule(
                workspace_id,
                required_fact_fields=["flag"],
                condition={"field": "flag", "operator": "eq", "value": True},
                result={"risk_level": "high"},
            ),
        )
        generated = assessment_uc.generate(
            case_id,
            "github:editor",
            ruleset_version="synthetic-v1",
        )
        document = assessment_uc._documents.get("doc_flag")
        assert document is not None
        assessment_uc._documents.update_document(
            document.model_copy(update={"current_version_id": "ver_new"})
        )

        with pytest.raises(InvalidDocumentContent, match="失效"):
            assessment_uc.review(
                generated.assessment.assessment_id,
                "github:reviewer",
                decision="approved",
                comment="尝试批准漂移引用",
            )

        case = case_repo.get(case_id)
        assert case is not None
        assert case.status == "review_required"
        current = assessment_uc.get(
            generated.assessment.assessment_id,
            "github:reviewer",
        )
        assert current.assessment.status == "review_required"

    def test_clause_snapshot_mismatch_blocks_approval(self) -> None:
        (
            assessment_uc,
            policy_uc,
            fact_repo,
            _,
            assessment_repo,
            workspace_id,
            case_id,
        ) = _setup()
        fact_repo.create(_confirmed_fact(case_id, "flag", True), [])
        _publish_rule(
            policy_uc,
            workspace_id,
            _rule(
                workspace_id,
                required_fact_fields=["flag"],
                condition={"field": "flag", "operator": "eq", "value": True},
                result={"risk_level": "high"},
            ),
        )
        generated = assessment_uc.generate(
            case_id,
            "github:editor",
            ruleset_version="synthetic-v1",
        )
        tampered = generated.model_copy(
            update={
                "findings": [
                    generated.findings[0].model_copy(update={"clause_ids": ["clause_tampered"]})
                ]
            }
        )
        assessment_repo._bundles[generated.assessment.assessment_id] = tampered

        with pytest.raises(InvalidDocumentContent, match="clause_ids"):
            assessment_uc.review(
                generated.assessment.assessment_id,
                "github:reviewer",
                decision="approved",
                comment="错误法条引用",
            )

    def test_rejection_returns_case_to_ready_for_assessment(self) -> None:
        (
            assessment_uc,
            policy_uc,
            fact_repo,
            case_repo,
            _,
            workspace_id,
            case_id,
        ) = _setup()
        fact_repo.create(_confirmed_fact(case_id, "flag", True), [])
        _publish_rule(
            policy_uc,
            workspace_id,
            _rule(
                workspace_id,
                required_fact_fields=["flag"],
                condition={"field": "flag", "operator": "eq", "value": True},
                result={"risk_level": "medium"},
            ),
        )
        generated = assessment_uc.generate(
            case_id,
            "github:editor",
            ruleset_version="synthetic-v1",
        )

        reviewed = assessment_uc.review(
            generated.assessment.assessment_id,
            "github:reviewer",
            decision="rejected",
            comment="需要补充传输链路材料",
        )

        assert reviewed.assessment.status == "rejected"
        case = case_repo.get(case_id)
        assert case is not None
        assert case.status == "ready_for_assessment"

    def test_editor_cannot_review_assessment(self) -> None:
        (
            assessment_uc,
            policy_uc,
            fact_repo,
            _,
            _,
            workspace_id,
            case_id,
        ) = _setup()
        fact_repo.create(_confirmed_fact(case_id, "flag", True), [])
        _publish_rule(
            policy_uc,
            workspace_id,
            _rule(
                workspace_id,
                required_fact_fields=["flag"],
                condition={"field": "flag", "operator": "eq", "value": True},
                result={},
            ),
        )
        generated = assessment_uc.generate(
            case_id,
            "github:editor",
            ruleset_version="synthetic-v1",
        )

        with pytest.raises(WorkspaceAccessDenied):
            assessment_uc.review(
                generated.assessment.assessment_id,
                "github:editor",
                decision="approved",
            )

    def test_missing_facts_cannot_be_approved(self) -> None:
        (
            assessment_uc,
            policy_uc,
            _,
            _,
            _,
            workspace_id,
            case_id,
        ) = _setup()
        _publish_rule(
            policy_uc,
            workspace_id,
            _rule(
                workspace_id,
                required_fact_fields=["missing_field"],
                condition={
                    "field": "missing_field",
                    "operator": "eq",
                    "value": True,
                },
                result={},
            ),
        )
        generated = assessment_uc.generate(
            case_id,
            "github:editor",
            ruleset_version="synthetic-v1",
        )

        with pytest.raises(ValueError, match="缺失事实"):
            assessment_uc.review(
                generated.assessment.assessment_id,
                "github:reviewer",
                decision="approved",
            )

    def test_superseded_assessment_cannot_be_reviewed(self) -> None:
        (
            assessment_uc,
            policy_uc,
            fact_repo,
            _,
            _,
            workspace_id,
            case_id,
        ) = _setup()
        fact_repo.create(_confirmed_fact(case_id, "flag", True), [])
        _publish_rule(
            policy_uc,
            workspace_id,
            _rule(
                workspace_id,
                required_fact_fields=["flag"],
                condition={"field": "flag", "operator": "eq", "value": True},
                result={},
            ),
        )
        first = assessment_uc.generate(
            case_id,
            "github:editor",
            ruleset_version="synthetic-v1",
        )
        assessment_uc.generate(
            case_id,
            "github:editor",
            ruleset_version="synthetic-v1",
        )

        with pytest.raises(AssessmentNotActive):
            assessment_uc.review(
                first.assessment.assessment_id,
                "github:reviewer",
                decision="approved",
            )
