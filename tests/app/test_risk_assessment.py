import json

import pytest

from app.use_cases.evidence_qa import EvidenceQAUseCase
from app.use_cases.risk_assessment import RiskAssessmentUseCase, render_risk_assessment
from domain.qa import EvidenceQACitation, EvidenceQAScope
from infra.storage._db import SqliteConnectionPool
from infra.storage.sqlite_risk_repo import SqliteRiskAssessmentRepo
from tests.app.test_runtime_control import _control, _run
from tests.fakes import (
    FakeChat,
    FakeClaimSupportVerifier,
    FakeEmbed,
    FakeEvidenceIndex,
    FakeEvidenceQAGenerator,
    FakeRetrieve,
    InMemoryConversationRunRepo,
    InMemoryDocumentRepo,
    InMemoryTaskRepo,
)


def _setup(tmp_path, *, citation_id="SRC-law", missing=None):
    draft = {
        "findings": [
            {
                "finding_id": "R1",
                "text": "需要核实接收方的安全措施",
                "severity": "medium",
                "citation_ids": [citation_id],
                "condition_message_ids": [],
                "recommendation": "补充安全措施材料",
            }
        ],
        "missing_information": missing or [],
    }
    chat = FakeChat(responses=[json.dumps(draft, ensure_ascii=False)])
    qa = EvidenceQAUseCase(
        retriever=FakeRetrieve(),
        evidence_index=FakeEvidenceIndex(),
        document_repo=InMemoryDocumentRepo(),
        embedder=FakeEmbed(),
        generator=FakeEvidenceQAGenerator(),
        support_verifier=FakeClaimSupportVerifier(),
    )
    pool = SqliteConnectionPool(str(tmp_path / "risk.sqlite3"))
    repo = SqliteRiskAssessmentRepo(pool)
    control_repo = InMemoryConversationRunRepo(InMemoryTaskRepo())
    run = _run()
    control_repo.create(run)
    control = _control(control_repo, run)
    sources = [
        EvidenceQACitation(
            citation_id="SRC-law",
            corpus="regulatory",
            source_id="law-1",
            source_name="测试法规",
            quote="接收方应提供安全措施。",
        ),
        EvidenceQACitation(
            citation_id="SRC-doc",
            corpus="knowledge",
            source_id="doc-1",
            source_name="测试材料",
            quote="接收方措施尚未说明。",
            owner_id=run.owner_id,
        ),
    ]
    return RiskAssessmentUseCase(chat, qa, repo), repo, pool, control, sources


def test_risk_result_is_verified_owner_scoped_and_derived_from_findings(tmp_path):
    service, repo, pool, control, sources = _setup(tmp_path)
    try:
        result = service.assess(
            owner_id="anon:alice",
            run_id=control.run_id,
            question="评估材料风险",
            scope=EvidenceQAScope(owner_id="anon:alice", corpora=["regulatory", "knowledge"]),
            citations=sources,
            user_context=[],
            runtime_control=control,
        )
        assert result.status == "assessed" and result.risk_level == "medium"
        assert result.findings[0].citation_ids == ["SRC-law"]
        assert "SRC-law" in render_risk_assessment(result)
        assert repo.for_run("anon:bob", control.run_id) is None
    finally:
        pool.close()


@pytest.mark.parametrize("missing,source_id", [(["境外接收方"], "SRC-law"), ([], "SRC-forged")])
def test_missing_conditions_and_forged_sources_never_become_low_risk(tmp_path, missing, source_id):
    service, repo, pool, control, sources = _setup(tmp_path, citation_id=source_id, missing=missing)
    try:
        result = service.assess(
            owner_id="anon:alice",
            run_id=control.run_id,
            question="评估材料风险",
            scope=EvidenceQAScope(owner_id="anon:alice", corpora=["regulatory", "knowledge"]),
            citations=sources,
            user_context=[],
            runtime_control=control,
        )
        assert result.status != "assessed" and result.risk_level == "unknown"
        if source_id == "SRC-forged":
            assert result.findings == []
    finally:
        pool.close()


def test_no_evidence_returns_missing_information_without_model_call(tmp_path):
    service, repo, pool, control, _ = _setup(tmp_path)
    try:
        result = service.assess(
            owner_id="anon:alice",
            run_id=control.run_id,
            question="评估风险",
            scope=EvidenceQAScope(owner_id="anon:alice", corpora=["regulatory"]),
            citations=[],
            user_context=[],
            runtime_control=control,
        )
        assert result.risk_level == "unknown"
        assert service._chat.calls == []
    finally:
        pool.close()


def test_model_cannot_smuggle_facts_in_missing_information(tmp_path):
    service, repo, pool, control, sources = _setup(tmp_path, missing=["所有公司都无需申报"])
    try:
        with pytest.raises(ValueError):
            service.assess(
                owner_id="anon:alice",
                run_id=control.run_id,
                question="评估风险",
                scope=EvidenceQAScope(owner_id="anon:alice", corpora=["regulatory", "knowledge"]),
                citations=sources,
                user_context=[],
                runtime_control=control,
            )
    finally:
        pool.close()


def test_unsupported_recommendation_does_not_remove_supported_finding(tmp_path):
    from domain.qa import ClaimSupportJudgement, ClaimSupportResult

    service, repo, pool, control, sources = _setup(tmp_path)
    service._qa._support_verifier = FakeClaimSupportVerifier(
        ClaimSupportResult(
            judgements=[
                ClaimSupportJudgement(claim_id="R1", supported=True, citation_ids=["SRC-law"]),
                ClaimSupportJudgement(
                    claim_id="R1:recommendation",
                    supported=False,
                    citation_ids=[],
                    reason="证据不支持该建议",
                ),
            ],
            unsupported_claim_ids=["R1:recommendation"],
            valid=False,
        )
    )
    try:
        result = service.assess(
            owner_id="anon:alice",
            run_id=control.run_id,
            question="评估材料风险",
            scope=EvidenceQAScope(owner_id="anon:alice", corpora=["regulatory", "knowledge"]),
            citations=sources,
            user_context=[],
            runtime_control=control,
        )
        assert len(result.findings) == 1
        assert result.findings[0].recommendation == ""
        assert result.status == "needs_information"
    finally:
        pool.close()
