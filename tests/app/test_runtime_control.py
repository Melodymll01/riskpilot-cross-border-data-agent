"""Unified per-run budgets, cancellation, timeout, and resume accounting."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.runtime_control import RunControl
from app.use_cases.evidence_search import EvidenceSearchUseCase
from domain.errors import ConversationRunStopped
from domain.models import ConversationRun
from tests.fakes import FakeEmbed, FakeEvidenceIndex, InMemoryConversationRunRepo, InMemoryTaskRepo


def _run(**updates) -> ConversationRun:
    now = time.time()
    values = {
        "run_id": "run-control",
        "thread_id": "thread-control",
        "owner_id": "anon:alice",
        "task_id": "task-control",
        "question": "问题",
        "input_message_id": "msg-control",
        "deadline_at": now + 30,
        "created_at": now,
        "updated_at": now,
    }
    values.update(updates)
    return ConversationRun(**values)


def _control(repo: InMemoryConversationRunRepo, run: ConversationRun, **timeouts) -> RunControl:
    return RunControl(
        run_id=run.run_id,
        owner_id=run.owner_id,
        run_repo=repo,
        model_call_timeout_seconds=timeouts.get("model", 1.0),
        tool_call_timeout_seconds=timeouts.get("tool", 1.0),
        poll_seconds=0.005,
    )


def test_model_call_limit_is_persisted_and_fails_closed() -> None:
    repo = InMemoryConversationRunRepo(InMemoryTaskRepo())
    run = _run(max_model_calls=1)
    repo.create(run)
    control = _control(repo, run)

    control.before_model_call(operation="agent_decision", stage="agent")
    with pytest.raises(ConversationRunStopped) as raised:
        control.before_model_call(operation="generation", stage="generation")

    assert raised.value.status == "budget_exhausted"
    assert raised.value.reason == "model_call_limit"
    stored = repo.get(run.run_id, run.owner_id)
    assert stored is not None
    assert stored.model_call_count == 1
    assert stored.status == "budget_exhausted"
    assert stored.answer_message_id is None


def test_invalid_model_stage_cannot_corrupt_persisted_run() -> None:
    repo = InMemoryConversationRunRepo(InMemoryTaskRepo())
    run = _run()
    repo.create(run)
    control = _control(repo, run)
    with pytest.raises(ValueError, match="stage"):
        control.before_model_call(operation="risk_analysis", stage="risk_analysis")
    stored = repo.get(run.run_id, run.owner_id)
    assert stored is not None and stored.stage == "agent"
    assert stored.model_call_count == 0


def test_operation_timeout_discards_late_model_result() -> None:
    repo = InMemoryConversationRunRepo(InMemoryTaskRepo())
    run = _run()
    repo.create(run)
    control = _control(repo, run, model=0.03)
    completed = threading.Event()

    def slow_model() -> str:
        time.sleep(0.12)
        completed.set()
        return "late"

    with pytest.raises(ConversationRunStopped) as raised:
        control.call_model(operation="generation", stage="generation", invoke=slow_model)

    assert raised.value.status == "timed_out"
    stored = repo.get(run.run_id, run.owner_id)
    assert stored is not None and stored.status == "timed_out"
    assert stored.answer_message_id is None
    assert completed.wait(timeout=1.0)
    assert repo.get(run.run_id, run.owner_id) == stored


def test_cancel_interrupts_waiting_tool_and_late_result_is_ignored() -> None:
    repo = InMemoryConversationRunRepo(InMemoryTaskRepo())
    run = _run()
    repo.create(run)
    control = _control(repo, run)
    started = threading.Event()
    release = threading.Event()

    def slow_tool() -> str:
        started.set()
        release.wait(timeout=1.0)
        return "late"

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            control.call_tool,
            operation="search_law",
            invoke=slow_tool,
        )
        assert started.wait(timeout=1.0)
        requested = repo.request_cancel(
            run.run_id,
            run.owner_id,
            reason="user_cancelled",
            now=time.time(),
        )
        assert requested is not None and requested.status == "cancel_requested"
        control.signal_cancel("user_cancelled")
        with pytest.raises(ConversationRunStopped) as raised:
            future.result(timeout=1.0)
        release.set()

    assert raised.value.status == "cancelled"
    stored = repo.get(run.run_id, run.owner_id)
    assert stored is not None and stored.status == "cancelled"
    assert stored.answer_message_id is None


def test_pause_time_and_call_counts_are_not_reset_on_resume() -> None:
    repo = InMemoryConversationRunRepo(InMemoryTaskRepo())
    run = _run(
        max_duration_seconds=10.0,
        remaining_seconds=10.0,
        deadline_at=110.0,
        max_model_calls=2,
        created_at=100.0,
        updated_at=100.0,
    )
    repo.create(run)
    clock = [101.0]
    control = RunControl(
        run_id=run.run_id,
        owner_id=run.owner_id,
        run_repo=repo,
        model_call_timeout_seconds=2.0,
        tool_call_timeout_seconds=2.0,
        poll_seconds=0.01,
        clock=lambda: clock[0],
    )
    control.before_model_call(operation="agent_decision", stage="agent")
    clock[0] = 103.0
    paused = control.interrupt()
    assert paused.remaining_seconds == pytest.approx(7.0)

    resumed = repo.claim_resume(run.run_id, run.owner_id, updated_at=1000.0)
    assert resumed is not None
    assert resumed.deadline_at == pytest.approx(1007.0)
    assert resumed.model_call_count == 1
    resumed_control = RunControl(
        run_id=run.run_id,
        owner_id=run.owner_id,
        run_repo=repo,
        model_call_timeout_seconds=2.0,
        tool_call_timeout_seconds=2.0,
        poll_seconds=0.01,
        clock=lambda: 1001.0,
    )
    assert (
        resumed_control.call_model(
            operation="agent_decision",
            stage="agent",
            invoke=lambda: "ok",
            retry=True,
        )
        == "ok"
    )
    with pytest.raises(ConversationRunStopped) as raised:
        resumed_control.before_model_call(operation="generation", stage="generation")
    assert raised.value.reason == "model_call_limit"
    stored = repo.get(run.run_id, run.owner_id)
    assert stored is not None and stored.model_retry_count == 1


def test_attachment_retrieval_embedding_consumes_model_budget() -> None:
    repo = InMemoryConversationRunRepo(InMemoryTaskRepo())
    run = _run()
    repo.create(run)
    control = _control(repo, run)
    search = EvidenceSearchUseCase(
        evidence_index=FakeEvidenceIndex(),
        embedder=FakeEmbed(),
    )

    assert (
        search.search(
            run.owner_id,
            document_ids=[],
            query="附件问题",
            runtime_control=control,
        )
        == []
    )

    stored = repo.get(run.run_id, run.owner_id)
    assert stored is not None and stored.model_call_count == 1
