"""跨租户、Citation、Trace 与长期记忆安全回归。"""

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
    AssessmentNotFound,
    CaseDocument,
    CaseFact,
    CaseFactEvidence,
    CaseNotFound,
    Document,
    DocumentParseSnapshot,
    DocumentVersion,
    InvalidDocumentContent,
    ParsedPage,
    PolicyRule,
    ProcessingJob,
    WorkspaceAccessDenied,
)
from infra.observability import sanitize_trace_metadata
from tests.fakes import (
    InMemoryAssessmentRepo,
    InMemoryCaseFactRepo,
    InMemoryCaseRepo,
    InMemoryDocumentRepo,
    InMemoryPolicyRuleRepo,
    InMemoryWorkspaceRepo,
)


class _SecuritySetup:
    def __init__(self) -> None:
        self.workspaces = InMemoryWorkspaceRepo()
        self.cases = InMemoryCaseRepo()
        self.facts = InMemoryCaseFactRepo()
        self.documents = InMemoryDocumentRepo()
        self.rules = InMemoryPolicyRuleRepo()
        self.assessments = InMemoryAssessmentRepo(self.cases)
        self.workspace_uc = WorkspaceManagementUseCase(self.workspaces)
        self.case_uc = CaseManagementUseCase(
            case_repo=self.cases,
            workspace_repo=self.workspaces,
        )
        self.policy_uc = PolicyManagementUseCase(
            rule_repo=self.rules,
            fact_repo=self.facts,
            case_management=self.case_uc,
            workspace_management=self.workspace_uc,
        )
        self.assessment_uc = AssessmentManagementUseCase(
            assessment_repo=self.assessments,
            fact_repo=self.facts,
            document_repo=self.documents,
            case_management=self.case_uc,
            workspace_management=self.workspace_uc,
            policy_management=self.policy_uc,
        )
        ws_a = self.workspace_uc.create_workspace("github:alice", name="A")
        ws_b = self.workspace_uc.create_workspace("github:bob", name="B")
        self.workspace_uc.add_or_update_member(
            ws_a.workspace_id,
            "github:alice",
            user_id="github:editor",
            role="editor",
        )
        self.case_a = self.case_uc.create_case(
            "github:alice",
            workspace_id=ws_a.workspace_id,
            title="A Case",
            assessment_date=date(2026, 8, 17),
        )
        self.case_b = self.case_uc.create_case(
            "github:bob",
            workspace_id=ws_b.workspace_id,
            title="B Case",
            assessment_date=date(2026, 8, 17),
        )
        for actor, case in (
            ("github:alice", self.case_a),
            ("github:bob", self.case_b),
        ):
            self.case_uc.transition_case(case.case_id, actor, "collecting")
            self.case_uc.transition_case(case.case_id, actor, "ready_for_assessment")
        self._seed_case_a_assessment(ws_a.workspace_id)

    def _seed_case_a_assessment(self, workspace_id: str) -> None:
        text = "材料确认 flag 为 true。"
        sha256 = hashlib.sha256(text.encode()).hexdigest()
        self.documents.create_upload(
            Document(
                document_id="doc_a",
                workspace_id=workspace_id,
                logical_name="a.txt",
                document_type="case_material",
                status="ready",
                created_by="github:alice",
                current_version_id="ver_a",
                created_at=100.0,
                updated_at=101.0,
            ),
            DocumentVersion(
                version_id="ver_a",
                document_id="doc_a",
                version_number=1,
                object_key="objects/a.txt",
                sha256=sha256,
                mime_type="text/plain",
                size_bytes=len(text.encode()),
                parser_version="test",
                page_count=1,
                created_at=100.0,
            ),
            CaseDocument(
                case_id=self.case_a.case_id,
                document_id="doc_a",
                added_by="github:alice",
                added_at=100.0,
            ),
            ProcessingJob(
                job_id="job_a",
                document_version_id="ver_a",
                status="completed",
                current_stage="ready",
                progress=1.0,
                created_at=100.0,
                updated_at=101.0,
                started_at=100.0,
                completed_at=101.0,
            ),
        )
        self.documents._snapshots["ver_a"] = DocumentParseSnapshot(
            snapshot_id="snapshot_a",
            document_version_id="ver_a",
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
        fact = CaseFact(
            fact_id="fact_a",
            case_id=self.case_a.case_id,
            field_name="flag",
            value=True,
            status="confirmed",
            source_type="document",
            confidence=1.0,
            criticality="critical",
            created_by="github:alice",
            confirmed_by="github:alice",
            confirmed_at=101.0,
            created_at=100.0,
            updated_at=101.0,
        )
        self.facts.create(
            fact,
            [
                CaseFactEvidence(
                    evidence_id="evidence_a",
                    case_id=self.case_a.case_id,
                    fact_id=fact.fact_id,
                    fact_version=1,
                    document_id="doc_a",
                    document_version_id="ver_a",
                    page_number=1,
                    quote="flag 为 true",
                    confidence=1.0,
                    created_at=101.0,
                )
            ],
        )
        rule = PolicyRule(
            workspace_id=workspace_id,
            rule_id="RULE-A",
            ruleset_version="rules-a",
            jurisdiction="CN",
            effective_from=date(2026, 1, 1),
            status="draft",
            required_fact_fields=["flag"],
            condition={"field": "flag", "operator": "eq", "value": True},
            result={"risk_level": "high", "required_actions": ["人工审核"]},
            source_clause_ids=["clause-a"],
        )
        self.policy_uc.create_rule(workspace_id, "github:alice", rule)
        self.policy_uc.publish_rule(
            workspace_id,
            "github:alice",
            rule_id=rule.rule_id,
            ruleset_version=rule.ruleset_version,
        )
        self.bundle = self.assessment_uc.generate(
            self.case_a.case_id,
            "github:alice",
            ruleset_version="rules-a",
        )


def test_cross_workspace_case_and_assessment_are_hidden() -> None:
    setup = _SecuritySetup()

    with pytest.raises(CaseNotFound):
        setup.case_uc.get_case(setup.case_a.case_id, "github:bob")
    with pytest.raises(AssessmentNotFound):
        setup.assessment_uc.get(
            setup.bundle.assessment.assessment_id,
            "github:bob",
        )


def test_forged_citation_blocks_reference_verification() -> None:
    setup = _SecuritySetup()
    citation = setup.bundle.evidence_citations[0]
    setup.assessments._bundles[setup.bundle.assessment.assessment_id] = setup.bundle.model_copy(
        update={"evidence_citations": [citation.model_copy(update={"quote": "伪造且不存在的引用"})]}
    )

    with pytest.raises(InvalidDocumentContent, match="不一致|原文"):
        setup.assessment_uc.verify_references(
            setup.bundle.assessment.assessment_id,
            "github:alice",
        )


def test_editor_or_agent_identity_cannot_approve_assessment() -> None:
    setup = _SecuritySetup()

    with pytest.raises(WorkspaceAccessDenied):
        setup.assessment_uc.review(
            setup.bundle.assessment.assessment_id,
            "github:editor",
            decision="approved",
            comment="agent tries to approve itself",
        )


def test_trace_drops_headers_cookies_prompts_and_document_content() -> None:
    secret = "Bearer top-secret-token"
    sanitized = sanitize_trace_metadata(
        {
            "run_id": "run_001",
            "status": "failed",
            "authorization": secret,
            "cookie": "session=secret",
            "raw_prompt": "ignore previous rules",
            "document_text": "完整案件正文",
            "chain_of_thought": "private reasoning",
            "error": "包含案件正文的异常",
        },
        hash_salt="phase6-test-salt-123456",
    )

    assert sanitized["run_id_hash"] != "run_001"
    assert sanitized["status"] == "failed"
    for forbidden in (
        "authorization",
        "cookie",
        "raw_prompt",
        "document_text",
        "chain_of_thought",
        "error",
    ):
        assert forbidden not in sanitized
    assert secret not in str(sanitized)
