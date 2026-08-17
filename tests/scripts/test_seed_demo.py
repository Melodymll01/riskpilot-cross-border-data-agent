"""三类 Seed Demo 的幂等与业务状态测试。"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from langchain_core.messages import AIMessage

from app.container import AppContainer
from config import Settings
from infra.agents import DeterministicEvidencePlanner
from infra.qa import SafeEmptyFactProposalGenerator
from infra.workflows import LangGraphWorkflowRuntime
from scripts.seed_demo import (
    DEMO_CASE_A_ID,
    DEMO_CASE_B_ID,
    DEMO_CASE_C_ID,
    DEMO_CASE_IDS,
    DEMO_WORKSPACE_ID,
    _document_ids,
    seed_demo,
)
from tests.fakes import (
    FakeAuditLogRepo,
    FakeAuth,
    FakeClaimSupportVerifier,
    FakeDocumentLoader,
    FakeDocumentParser,
    FakeEmbed,
    FakeEvidenceChunker,
    FakeEvidenceIndex,
    FakeEvidenceQAGenerator,
    FakeJobDispatcher,
    FakeKbRepo,
    FakeObjectStore,
    FakeReadiness,
    FakeRetrieve,
    FakeRiskProfile,
    FakeToolCallingModel,
    FakeTrace,
    FakeVisualEmbedder,
    FakeVisualIndex,
    FakeWebSearch,
    InMemoryAgentRunRepo,
    InMemoryAssessmentRepo,
    InMemoryCaseFactRepo,
    InMemoryCaseRepo,
    InMemoryDocumentRepo,
    InMemoryPolicyRuleRepo,
    InMemoryTaskRepo,
    InMemoryUserRepo,
    InMemoryWorkspaceRepo,
)
from tests.fakes.fake_chat import FakeChat


def _container(tmp_path: Path) -> tuple[AppContainer, FakeJobDispatcher]:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        storage_backend="postgres",
        vector_backend="pgvector",
        task_backend="celery",
        object_store_backend="s3",
        llm_provider="local",
        embed_provider="deterministic",
        embedding_dimensions=8,
        agent_planner_backend="deterministic",
        fact_proposal_backend="safe_empty",
        memory_enabled=False,
        prometheus_enabled=False,
        langgraph_checkpoint_db_path=str(tmp_path / "seed-checkpoint.sqlite3"),
        object_store_dir=str(tmp_path / "objects"),
    )
    document_repo = InMemoryDocumentRepo()
    case_repo = InMemoryCaseRepo()
    dispatcher = FakeJobDispatcher()
    container = AppContainer(
        settings,
        agent_run_repo=InMemoryAgentRunRepo(),
        assessment_repo=InMemoryAssessmentRepo(case_repo),
        user_repo=InMemoryUserRepo(),
        task_repo=InMemoryTaskRepo(),
        workspace_repo=InMemoryWorkspaceRepo(),
        case_repo=case_repo,
        case_fact_repo=InMemoryCaseFactRepo(),
        policy_rule_repo=InMemoryPolicyRuleRepo(),
        document_repo=document_repo,
        object_store=FakeObjectStore(),
        job_dispatcher=dispatcher,
        document_parser=FakeDocumentParser(),
        evidence_chunker=FakeEvidenceChunker(),
        evidence_index=FakeEvidenceIndex(document_repo),
        workflow_runtime=LangGraphWorkflowRuntime(
            str(tmp_path / "seed-checkpoint.sqlite3"),
            planner=DeterministicEvidencePlanner(),
        ),
        audit_log=FakeAuditLogRepo(),
        embedder=FakeEmbed(),
        chat=FakeChat(),
        evidence_qa_generator=FakeEvidenceQAGenerator(),
        claim_support_verifier=FakeClaimSupportVerifier(),
        fact_proposal_generator=SafeEmptyFactProposalGenerator(),
        retriever=FakeRetrieve(),
        web_search=FakeWebSearch(),
        risk_profile=FakeRiskProfile(),
        readiness=FakeReadiness(),
        trace=FakeTrace(),
        kb_repo=FakeKbRepo(),
        document_loader=FakeDocumentLoader(),
        auth=FakeAuth(),
        agent_model=FakeToolCallingModel(responses=[AIMessage(content="done")]),
        visual_index=FakeVisualIndex(),
        visual_embedder=FakeVisualEmbedder(),
        evidence_planner=DeterministicEvidencePlanner(),
    )
    return container, dispatcher


def _run_workers(
    container: AppContainer,
    case_ids: Iterable[str],
    _timeout_seconds: float,
) -> None:
    for case_id in case_ids:
        job_id = _document_ids(case_id).job_id
        job = container.document_repo.get_job(job_id)
        if job is not None and job.status == "completed":
            continue
        container.document_processing_worker.run_parse_stage(job_id)
        container.evidence_index_worker.run(job_id)


def test_seed_demo_creates_three_real_business_scenarios(tmp_path: Path) -> None:
    container, dispatcher = _container(tmp_path)

    result = seed_demo(container, wait_for_ready=_run_workers)

    assert result["workspace_id"] == DEMO_WORKSPACE_ID
    assert set(result["cases"]) == {
        "happy_path",
        "human_in_the_loop",
        "failure_recovery",
    }
    assert container.workspace_repo.get(DEMO_WORKSPACE_ID) is not None
    assert {item.case_id for item in container.case_repo.list_for_workspace(DEMO_WORKSPACE_ID)} == (
        set(DEMO_CASE_IDS)
    )
    happy = result["cases"]["happy_path"]
    hitl = result["cases"]["human_in_the_loop"]
    failure = result["cases"]["failure_recovery"]
    assert happy["run_status"] == "waiting_for_review"
    assert happy["current_stage"] == "human_review"
    assert hitl["run_status"] == "waiting_for_user"
    assert hitl["current_stage"] == "human_fact_confirmation"
    assert failure["job_status"] == "failed"
    assert failure["run_id"] is None
    assert container.case_fact_repo.get("fact_demo_important_data") is not None
    assert len(dispatcher.enqueued) == 2
    assert Path(result["local_manifest"]).exists()


def test_seed_demo_is_idempotent_and_requeues_incomplete_documents(tmp_path: Path) -> None:
    container, dispatcher = _container(tmp_path)
    first = seed_demo(container, wait_for_ready=_run_workers)

    second = seed_demo(container, wait_for_ready=_run_workers)

    assert first["cases"]["happy_path"]["run_id"] == second["cases"]["happy_path"]["run_id"]
    assert (
        first["cases"]["human_in_the_loop"]["run_id"]
        == second["cases"]["human_in_the_loop"]["run_id"]
    )
    assert len(container.case_repo.list_for_workspace(DEMO_WORKSPACE_ID)) == 3
    assert len(container.policy_rule_repo.list_rules(workspace_id=DEMO_WORKSPACE_ID)) == 1
    assert len(container.case_fact_repo.list_for_case(DEMO_CASE_A_ID)) == 1
    assert len(container.agent_run_repo.list_for_case(DEMO_CASE_A_ID)) == 1
    assert len(container.agent_run_repo.list_for_case(DEMO_CASE_B_ID)) == 1
    assert len(dispatcher.enqueued) == 2
    assert container.document_repo.get_job(_document_ids(DEMO_CASE_C_ID).job_id).retry_count == 0


def test_failure_recovery_demo_can_retry_through_real_use_case(tmp_path: Path) -> None:
    container, dispatcher = _container(tmp_path)
    seed_demo(container, wait_for_ready=_run_workers)
    job_id = _document_ids(DEMO_CASE_C_ID).job_id

    retried = container.document_management.retry_job(
        job_id,
        "github:riskpilot-demo-editor",
    )

    assert retried.status == "queued"
    assert retried.retry_count == 1
    assert dispatcher.enqueued[-1] == (job_id, 1)
