"""SQLAlchemy 核心 Repository contract。

默认使用 SQLite 内存 Engine，验证 ORM 映射和 Port 语义；设置
``TEST_POSTGRES_URL`` 时同一套测试运行在真实 PostgreSQL。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from domain import (
    ActionItem,
    AgentRun,
    AgentRunConflict,
    AgentRunRepoPort,
    Assessment,
    AssessmentBundle,
    AssessmentEvidenceCitation,
    AssessmentRepoPort,
    Case,
    CaseDocument,
    CaseFact,
    CaseFactEvidence,
    CaseFactRepoPort,
    CaseRepoPort,
    Document,
    DocumentRepoPort,
    DocumentVersion,
    EvidenceChunk,
    EvidenceIndexPort,
    Finding,
    PolicyEvaluation,
    PolicyRule,
    PolicyRuleRepoPort,
    ProcessingJob,
    RunCheckpoint,
    RunEvent,
    Workspace,
    WorkspaceMembership,
    WorkspaceRepoPort,
)
from infra.storage.sqlalchemy import (
    Base,
    SqlAlchemyAgentRunRepo,
    SqlAlchemyAssessmentRepo,
    SqlAlchemyCaseFactRepo,
    SqlAlchemyCaseRepo,
    SqlAlchemyDatabase,
    SqlAlchemyDocumentRepo,
    SqlAlchemyEvidenceIndex,
    SqlAlchemyPolicyRuleRepo,
    SqlAlchemyWorkspaceRepo,
)
from infra.storage.sqlalchemy.mapping import require_datetime
from infra.storage.sqlalchemy.models import DocumentVersionRow, EvidenceChunkRow

_POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")
_EVIDENCE_DIMENSIONS = 2048 if _POSTGRES_URL else 2


def _embedding(first: float, second: float) -> list[float]:
    return [first, second, *([0.0] * (_EVIDENCE_DIMENSIONS - 2))]


@pytest.fixture
def database() -> Iterator[SqlAlchemyDatabase]:
    url = _POSTGRES_URL or "sqlite://"
    database = SqlAlchemyDatabase(url)
    Base.metadata.drop_all(database.engine)
    Base.metadata.create_all(database.engine)
    yield database
    database.dispose()


@pytest.fixture
def repos(database: SqlAlchemyDatabase):
    return {
        "workspace": SqlAlchemyWorkspaceRepo(database),
        "case": SqlAlchemyCaseRepo(database),
        "document": SqlAlchemyDocumentRepo(database),
        "evidence": SqlAlchemyEvidenceIndex(database),
        "fact": SqlAlchemyCaseFactRepo(database),
        "policy": SqlAlchemyPolicyRuleRepo(database),
        "assessment": SqlAlchemyAssessmentRepo(database),
        "run": SqlAlchemyAgentRunRepo(database),
    }


def _workspace() -> Workspace:
    return Workspace(
        workspace_id="ws_001",
        name="跨境合规组",
        created_by="github:alice",
        created_at=100.0,
        updated_at=100.0,
    )


def _membership(user_id: str = "github:alice", role: str = "admin") -> WorkspaceMembership:
    return WorkspaceMembership(
        workspace_id="ws_001",
        user_id=user_id,
        role=role,
        joined_at=100.0,
    )


def _case() -> Case:
    return Case(
        case_id="case_001",
        workspace_id="ws_001",
        title="德国总部数据出境",
        assessment_date=date(2026, 8, 17),
        owner_id="github:alice",
        created_at=100.0,
        updated_at=100.0,
    )


def _seed_case(repos) -> Case:
    repos["workspace"].create(_workspace(), _membership())
    case = _case()
    repos["case"].create(case)
    return case


def _upload_graph() -> tuple[Document, DocumentVersion, CaseDocument, ProcessingJob]:
    version = DocumentVersion(
        version_id="ver_001",
        document_id="doc_001",
        version_number=1,
        object_key="ws_001/doc_001/ver_001/source.txt",
        sha256="a" * 64,
        mime_type="text/plain",
        size_bytes=10,
        created_at=100.0,
    )
    return (
        Document(
            document_id="doc_001",
            workspace_id="ws_001",
            logical_name="合同.txt",
            document_type="case_material",
            status="ready",
            created_by="github:alice",
            current_version_id=version.version_id,
            created_at=100.0,
            updated_at=100.0,
        ),
        version,
        CaseDocument(
            case_id="case_001",
            document_id="doc_001",
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


def _fact() -> CaseFact:
    return CaseFact(
        fact_id="fact_001",
        case_id="case_001",
        field_name="important_data_involved",
        value=True,
        source_type="document",
        confidence=0.9,
        criticality="critical",
        created_by="github:alice",
        created_at=100.0,
        updated_at=100.0,
    )


def _evidence(fact: CaseFact) -> CaseFactEvidence:
    return CaseFactEvidence(
        evidence_id=f"evidence_{fact.version}",
        case_id=fact.case_id,
        fact_id=fact.fact_id,
        fact_version=fact.version,
        document_id="doc_001",
        document_version_id="ver_001",
        page_number=1,
        quote="涉及重要数据",
        confidence=0.9,
        created_at=fact.updated_at,
    )


def _initial_run(run_id: str = "run_001"):
    run = AgentRun(
        run_id=run_id,
        workspace_id="ws_001",
        case_id="case_001",
        workflow_type="case_assessment",
        thread_id=f"thread_{run_id}",
        checkpoint_id=f"checkpoint_{run_id}_1",
        current_stage="queued",
        created_by="github:alice",
        created_at=100.0,
        updated_at=100.0,
    )
    checkpoint = RunCheckpoint(
        checkpoint_id=run.checkpoint_id or "",
        run_id=run.run_id,
        thread_id=run.thread_id,
        version=1,
        stage="queued",
        state={"case_id": run.case_id},
        created_at=100.0,
    )
    event = RunEvent(
        event_id=f"event_{run_id}_1",
        run_id=run.run_id,
        sequence=1,
        event_type="run_started",
        stage="queued",
        created_at=100.0,
    )
    return run, checkpoint, event


def _assessment_bundle() -> AssessmentBundle:
    assessment = Assessment(
        assessment_id="assessment_001",
        case_id="case_001",
        version=1,
        status="review_required",
        assessment_date=date(2026, 8, 17),
        jurisdiction="CN",
        ruleset_version="synthetic-v1",
        fact_versions={"fact_001": 1},
        policy_evaluations=[
            PolicyEvaluation(
                rule_id="rule_001",
                ruleset_version="synthetic-v1",
                status="triggered",
                consumed_fact_versions={"important_data_involved": 1},
                result={"risk_level": "high"},
                source_clause_ids=["clause_001"],
            )
        ],
        risk_level="high",
        candidate_paths=["security_assessment"],
        created_at=100.0,
        updated_at=100.0,
    )
    citation = AssessmentEvidenceCitation(
        citation_id="citation_001",
        assessment_id=assessment.assessment_id,
        source_evidence_id="evidence_1",
        fact_id="fact_001",
        fact_version=1,
        document_id="doc_001",
        document_version_id="ver_001",
        page_number=1,
        quote="涉及重要数据",
        source_sha256="a" * 64,
        created_at=100.0,
    )
    finding = Finding(
        finding_id="finding_001",
        assessment_id=assessment.assessment_id,
        finding_type="rule_trigger",
        severity="high",
        title="触发安全评估",
        fact_ids=["fact_001"],
        evidence_ids=[citation.citation_id],
        rule_ids=["rule_001"],
        clause_ids=["clause_001"],
    )
    action = ActionItem(
        action_id="action_001",
        assessment_id=assessment.assessment_id,
        title="准备申报材料",
        priority="high",
        related_finding_ids=[finding.finding_id],
    )
    return AssessmentBundle(
        assessment=assessment,
        findings=[finding],
        action_items=[action],
        evidence_citations=[citation],
    )


def test_protocols_and_schema(database: SqlAlchemyDatabase, repos) -> None:
    assert isinstance(repos["workspace"], WorkspaceRepoPort)
    assert isinstance(repos["case"], CaseRepoPort)
    assert isinstance(repos["document"], DocumentRepoPort)
    assert isinstance(repos["evidence"], EvidenceIndexPort)
    assert isinstance(repos["fact"], CaseFactRepoPort)
    assert isinstance(repos["policy"], PolicyRuleRepoPort)
    assert isinstance(repos["assessment"], AssessmentRepoPort)
    assert isinstance(repos["run"], AgentRunRepoPort)
    assert database.ping() is True
    assert "uq_agent_runs_active_case_workflow" in {
        item["name"] for item in inspect(database.engine).get_indexes("agent_runs")
    }
    if database.engine.dialect.name == "postgresql":
        evidence_indexes = {
            item["name"]: item for item in inspect(database.engine).get_indexes("evidence_chunks")
        }
        assert "ix_evidence_chunks_embedding_hnsw_2048" in evidence_indexes
        assert "ix_evidence_chunks_search_tokens_fts" in evidence_indexes
        assert (
            evidence_indexes["ix_evidence_chunks_embedding_hnsw_2048"]["dialect_options"][
                "postgresql_using"
            ]
            == "hnsw"
        )


def test_workspace_case_document_fact_round_trip(repos) -> None:
    case = _seed_case(repos)
    repos["workspace"].upsert_membership(_membership("github:bob", "viewer"))
    assert repos["workspace"].get_membership("ws_001", "github:bob") is not None
    assert repos["case"].get(case.case_id) == case

    graph = _upload_graph()
    repos["document"].create_upload(*graph)
    assert repos["document"].get("doc_001") == graph[0]
    assert repos["document"].get_version("ver_001") == graph[1]

    fact = _fact()
    repos["fact"].create(fact, [_evidence(fact)])
    revised = fact.propose_revision(
        value=False,
        source_type="document",
        confidence=0.8,
        actor_id="github:editor",
        at=101.0,
    )
    repos["fact"].save_revision(revised, [_evidence(revised)])
    assert repos["fact"].get_version(fact.fact_id, 1) == fact
    assert repos["fact"].get_version(fact.fact_id, 2) == revised


def test_evidence_index_enforces_case_scope(database, repos) -> None:
    _seed_case(repos)
    graph = _upload_graph()
    repos["document"].create_upload(*graph)
    chunk = EvidenceChunk(
        chunk_id="chunk_001",
        workspace_id="ws_001",
        case_id="case_001",
        document_id="doc_001",
        document_version_id="ver_001",
        page_number=1,
        chunk_index=0,
        text="涉及重要数据，应当申报安全评估",
        source_sha256="a" * 64,
        created_at=100.0,
    )
    repos["evidence"].replace_version_chunks("ver_001", [chunk], [_embedding(1.0, 0.0)])
    assert repos["evidence"].count_version("ver_001") == 1

    if _POSTGRES_URL:
        with database.session() as session:
            session.add(
                EvidenceChunkRow(
                    chunk_id="chunk_wrong_dimension",
                    workspace_id="ws_001",
                    case_id="case_001",
                    document_id="doc_001",
                    document_version_id="ver_001",
                    page_number=1,
                    chunk_index=1,
                    text="重要数据 安全评估",
                    search_tokens="重要 数据 安全 评估",
                    source_sha256="a" * 64,
                    embedding=[1.0, 0.0],
                    created_at=require_datetime(100.0),
                )
            )

    assert [
        hit.chunk.chunk_id
        for hit in repos["evidence"].search(
            workspace_id="ws_001",
            case_id="case_001",
            query="重要数据 安全评估",
            query_embedding=_embedding(1.0, 0.0),
        )
    ] == ["chunk_001"]
    assert (
        repos["evidence"].search(
            workspace_id="ws_001",
            case_id="case_other",
            query="重要数据 安全评估",
            query_embedding=_embedding(1.0, 0.0),
        )
        == []
    )


def test_evidence_search_excludes_non_current_document_versions(database, repos) -> None:
    _seed_case(repos)
    document, version_1, binding, job = _upload_graph()
    repos["document"].create_upload(document, version_1, binding, job)
    old_chunk = EvidenceChunk(
        chunk_id="chunk_old",
        workspace_id="ws_001",
        case_id="case_001",
        document_id="doc_001",
        document_version_id="ver_001",
        page_number=1,
        chunk_index=0,
        text="旧版本包含重要数据",
        source_sha256="a" * 64,
        created_at=100.0,
    )
    repos["evidence"].replace_version_chunks(
        "ver_001",
        [old_chunk],
        [_embedding(1.0, 0.0)],
    )

    version_2 = DocumentVersion(
        version_id="ver_002",
        document_id="doc_001",
        version_number=2,
        object_key="ws_001/doc_001/ver_002/source.txt",
        sha256="b" * 64,
        mime_type="text/plain",
        size_bytes=12,
        created_at=101.0,
    )
    with database.session() as session:
        session.add(
            DocumentVersionRow(
                version_id=version_2.version_id,
                document_id=version_2.document_id,
                version_number=version_2.version_number,
                object_key=version_2.object_key,
                sha256=version_2.sha256,
                mime_type=version_2.mime_type,
                size_bytes=version_2.size_bytes,
                parser_version=version_2.parser_version,
                page_count=version_2.page_count,
                created_at=require_datetime(version_2.created_at),
            )
        )
    repos["document"].update_document(
        document.model_copy(update={"current_version_id": "ver_002", "updated_at": 101.0})
    )
    new_chunk = old_chunk.model_copy(
        update={
            "chunk_id": "chunk_current",
            "document_version_id": "ver_002",
            "text": "当前版本包含个人信息",
            "source_sha256": "b" * 64,
            "created_at": 101.0,
        }
    )
    repos["evidence"].replace_version_chunks(
        "ver_002",
        [new_chunk],
        [_embedding(0.0, 1.0)],
    )

    hits = repos["evidence"].search(
        workspace_id="ws_001",
        case_id="case_001",
        query="重要数据",
        query_embedding=_embedding(1.0, 0.0),
    )

    assert [hit.chunk.chunk_id for hit in hits] == ["chunk_current"]


def test_policy_assessment_and_review_are_atomic(repos) -> None:
    case = _seed_case(repos)
    rule = PolicyRule(
        workspace_id="ws_001",
        rule_id="rule_001",
        ruleset_version="synthetic-v1",
        jurisdiction="CN",
        effective_from=date(2026, 1, 1),
        status="published",
        required_fact_fields=["important_data_involved"],
        condition={"field": "important_data_involved", "operator": "eq", "value": True},
        result={"candidate_path": "security_assessment"},
        source_clause_ids=["clause_001"],
    )
    repos["policy"].create(rule)
    assert repos["policy"].get("ws_001", "rule_001", "synthetic-v1") == rule

    bundle = _assessment_bundle()
    review_case = case.model_copy(
        update={
            "status": "review_required",
            "active_assessment_id": bundle.assessment.assessment_id,
            "updated_at": 101.0,
        }
    )
    repos["assessment"].create_version(bundle, None, review_case)
    assert repos["assessment"].get_active(case.case_id) == bundle
    approved = bundle.assessment.transition_to(
        "approved",
        actor_id="github:reviewer",
        at=102.0,
    )
    completed = review_case.transition_to("completed", at=102.0)
    repos["assessment"].save_review(approved, completed)
    assert repos["assessment"].get(bundle.assessment.assessment_id).assessment == approved
    assert repos["case"].get(case.case_id).status == "completed"


def test_run_optimistic_lock_and_active_unique_constraint(repos) -> None:
    _seed_case(repos)
    run, checkpoint, event = _initial_run()
    repos["run"].create(run, checkpoint, event)

    duplicate, duplicate_checkpoint, duplicate_event = _initial_run("run_002")
    with pytest.raises((AgentRunConflict, IntegrityError)):
        repos["run"].create(duplicate, duplicate_checkpoint, duplicate_event)

    updated = run.start(
        checkpoint_id="checkpoint_run_001_2",
        stage="load_case",
        at=101.0,
    )
    progress_checkpoint = RunCheckpoint(
        checkpoint_id="checkpoint_run_001_2",
        run_id=run.run_id,
        thread_id=run.thread_id,
        version=2,
        stage="load_case",
        state={"next": "authorize"},
        created_at=101.0,
    )
    progress_event = RunEvent(
        event_id="event_run_001_2",
        run_id=run.run_id,
        sequence=2,
        event_type="stage_completed",
        stage="load_case",
        created_at=101.0,
    )
    repos["run"].save_progress(
        updated,
        progress_checkpoint,
        [progress_event],
        expected_revision=1,
    )
    stale_run = updated.model_copy(update={"checkpoint_id": "checkpoint_stale"})
    stale_checkpoint = progress_checkpoint.model_copy(update={"checkpoint_id": "checkpoint_stale"})
    with pytest.raises(AgentRunConflict):
        repos["run"].save_progress(
            stale_run,
            stale_checkpoint,
            [progress_event.model_copy(update={"event_id": "stale"})],
            expected_revision=1,
        )
    assert repos["run"].get(run.run_id) == updated


@pytest.mark.skipif(
    not _POSTGRES_URL,
    reason="需要 TEST_POSTGRES_URL 验证真实 PostgreSQL 并发约束",
)
def test_postgres_concurrent_active_run_creation_allows_only_one(
    database: SqlAlchemyDatabase,
    repos,
) -> None:
    _seed_case(repos)

    def create(run_id: str) -> str:
        run, checkpoint, event = _initial_run(run_id)
        try:
            SqlAlchemyAgentRunRepo(database).create(run, checkpoint, event)
        except (AgentRunConflict, IntegrityError):
            return "conflict"
        return "created"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(create, ("run_a", "run_b")))

    assert sorted(outcomes) == ["conflict", "created"]
