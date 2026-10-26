"""Persistent conversation run and attachment selection contracts."""

from __future__ import annotations

import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from domain.documents import Document, DocumentVersion, ProcessingJob
from domain.errors import ConversationRunConflict
from domain.models import Citation, ConversationRun, Message, Task
from infra.storage import SqliteConversationRunRepo, SqliteDocumentRepo, SqliteTaskRepo
from infra.storage._db import SqliteConnectionPool


@pytest.fixture
def repos(tmp_path):
    pool = SqliteConnectionPool(str(tmp_path / "runs.sqlite3"))
    return SqliteTaskRepo(pool), SqliteDocumentRepo(pool), SqliteConversationRunRepo(pool)


def _seed_task(task_repo: SqliteTaskRepo, owner_id: str = "alice") -> tuple[Task, Message]:
    now = time.time()
    task = Task(task_id="task-1", owner_id=owner_id, created_at=now, updated_at=now)
    task_repo.create(task)
    message = Message(msg_id="msg-1", task_id=task.task_id, role="user", content="问题")
    task_repo.append_message(message)
    return task, message


def _seed_document(document_repo: SqliteDocumentRepo, owner_id: str, document_id: str) -> None:
    now = time.time()
    version_id = f"version-{document_id}"
    document_repo.create_upload(
        Document(
            document_id=document_id,
            owner_id=owner_id,
            logical_name=f"{document_id}.txt",
            document_type="user_document",
            status="ready",
            created_by=owner_id,
            current_version_id=version_id,
            created_at=now,
            updated_at=now,
        ),
        DocumentVersion(
            version_id=version_id,
            document_id=document_id,
            version_number=1,
            object_key=f"{owner_id}/{document_id}/source.txt",
            sha256="a" * 64,
            mime_type="text/plain",
            size_bytes=1,
            created_at=now,
        ),
        ProcessingJob(
            job_id=f"job-{document_id}",
            document_version_id=version_id,
            status="completed",
            current_stage="ready",
            progress=1.0,
            created_at=now,
            updated_at=now,
            started_at=now,
            completed_at=now,
        ),
    )


def test_attachment_selection_replaces_in_order_and_is_owner_scoped(repos) -> None:
    task_repo, document_repo, _ = repos
    task, _ = _seed_task(task_repo)
    _seed_document(document_repo, "alice", "doc-a")
    _seed_document(document_repo, "alice", "doc-b")

    task_repo.set_attachment_ids(task.task_id, "alice", ["doc-b", "doc-a", "doc-b"])

    assert task_repo.list_attachment_ids(task.task_id, "alice") == ["doc-b", "doc-a"]
    with pytest.raises(PermissionError):
        task_repo.list_attachment_ids(task.task_id, "bob")
    task_repo.set_attachment_ids(task.task_id, "alice", [])
    assert task_repo.list_attachment_ids(task.task_id, "alice") == []


def test_run_round_trip_update_and_owner_isolation(repos) -> None:
    task_repo, _, run_repo = repos
    task, message = _seed_task(task_repo)
    now = time.time()
    run = ConversationRun(
        run_id="run-1",
        thread_id="thread-server-generated",
        owner_id="alice",
        task_id=task.task_id,
        question="问题",
        input_message_id=message.msg_id,
        attachment_doc_ids=["doc-a"],
        created_at=now,
        updated_at=now,
    )
    run_repo.create(run)

    assert run_repo.get(run.run_id, "bob") is None
    loaded = run_repo.get(run.run_id, "alice")
    assert loaded == run
    updated = run.model_copy(
        update={
            "status": "interrupted",
            "evidence": [{"citation_id": "SRC-1"}],
            "verified_context_message_ids": [message.msg_id],
            "updated_at": now + 1,
        }
    )
    run_repo.update(updated)
    assert run_repo.get_latest_for_task(task.task_id, "alice") == updated


def test_resume_claim_and_terminal_message_commit_are_conditional_and_atomic(repos) -> None:
    task_repo, _, run_repo = repos
    task, input_message = _seed_task(task_repo)
    now = time.time()
    interrupted = ConversationRun(
        run_id="run-resume",
        thread_id="thread-resume",
        owner_id="alice",
        task_id=task.task_id,
        question=input_message.content,
        input_message_id=input_message.msg_id,
        status="interrupted",
        created_at=now,
        updated_at=now,
    )
    run_repo.create(interrupted)

    claimed = run_repo.claim_resume(interrupted.run_id, "alice", updated_at=now + 1)
    assert claimed is not None and claimed.status == "running"
    assert run_repo.claim_resume(interrupted.run_id, "alice", updated_at=now + 2) is None

    answer = Message(
        msg_id="msg-answer",
        task_id=task.task_id,
        role="assistant",
        content="已验证回答",
        citations=[Citation(source_type="law", source_name="法规")],
        created_at=now + 3,
    )
    completed = claimed.model_copy(
        update={
            "status": "completed",
            "stage": "completed",
            "answer_message_id": answer.msg_id,
            "updated_at": now + 3,
        }
    )
    run_repo.commit_terminal(completed, answer)

    assert task_repo.list_messages(task.task_id) == [input_message, answer]
    persisted = run_repo.get(interrupted.run_id, "alice")
    assert persisted is not None
    assert persisted.status == "completed"
    assert persisted.answer_message_id == answer.msg_id
    assert persisted.deadline_at is None
    second_answer = answer.model_copy(update={"msg_id": "msg-second"})
    second_completed = completed.model_copy(update={"answer_message_id": second_answer.msg_id})
    with pytest.raises(ConversationRunConflict):
        run_repo.commit_terminal(second_completed, second_answer)
    assert task_repo.list_messages(task.task_id) == [input_message, answer]


def test_call_budget_cancel_and_pause_are_atomic(repos) -> None:
    task_repo, _, run_repo = repos
    task, message = _seed_task(task_repo)
    run = ConversationRun(
        run_id="run-control",
        thread_id="thread-control",
        owner_id="alice",
        task_id=task.task_id,
        question=message.content,
        input_message_id=message.msg_id,
        max_duration_seconds=10.0,
        remaining_seconds=10.0,
        deadline_at=110.0,
        max_model_calls=1,
        max_tool_calls=1,
        created_at=100.0,
        updated_at=100.0,
    )
    run_repo.create(run)

    reserved = run_repo.reserve_call(
        run.run_id,
        "alice",
        kind="model",
        stage="agent",
        now=101.0,
    )
    assert reserved is not None and reserved.model_call_count == 1
    assert (
        run_repo.reserve_call(
            run.run_id,
            "alice",
            kind="model",
            stage="agent",
            now=102.0,
        )
        is None
    )
    paused = run_repo.pause(run.run_id, "alice", now=103.0)
    assert paused is not None and paused.status == "interrupted"
    assert paused.remaining_seconds == pytest.approx(7.0)

    resumed = run_repo.claim_resume(run.run_id, "alice", updated_at=1000.0)
    assert resumed is not None and resumed.deadline_at == pytest.approx(1007.0)
    assert resumed.model_call_count == 1

    requested = run_repo.request_cancel(
        run.run_id,
        "alice",
        reason="user_cancelled",
        now=1001.0,
    )
    assert requested is not None and requested.status == "cancel_requested"
    repeated = run_repo.request_cancel(
        run.run_id,
        "alice",
        reason="user_cancelled",
        now=1002.0,
    )
    assert repeated == requested

    cancelled = run_repo.finish_active(
        run.run_id,
        "alice",
        status="cancelled",
        reason="user_cancelled",
        error_type=None,
        now=1003.0,
    )
    assert cancelled is not None and cancelled.status == "cancelled"
    assert cancelled.remaining_seconds == pytest.approx(4.0)
    assert cancelled.finished_at == 1003.0


def test_cancel_wins_against_terminal_message_commit(repos) -> None:
    task_repo, _, run_repo = repos
    task, input_message = _seed_task(task_repo)
    now = time.time()
    run = ConversationRun(
        run_id="run-cancel-race",
        thread_id="thread-cancel-race",
        owner_id="alice",
        task_id=task.task_id,
        question=input_message.content,
        input_message_id=input_message.msg_id,
        deadline_at=now + 30,
        created_at=now,
        updated_at=now,
    )
    run_repo.create(run)
    requested = run_repo.request_cancel(
        run.run_id,
        "alice",
        reason="user_cancelled",
        now=now + 1,
    )
    assert requested is not None and requested.status == "cancel_requested"
    answer = Message(
        msg_id="msg-too-late",
        task_id=task.task_id,
        role="assistant",
        content="迟到答案",
        created_at=now + 2,
    )
    completed = requested.model_copy(
        update={
            "status": "completed",
            "stage": "completed",
            "answer_message_id": answer.msg_id,
            "end_reason": "answered",
            "finished_at": now + 2,
            "updated_at": now + 2,
        }
    )

    with pytest.raises(ConversationRunConflict):
        run_repo.commit_terminal(completed, answer)

    assert task_repo.list_messages(task.task_id) == [input_message]
    current = run_repo.get(run.run_id, "alice")
    assert current is not None and current.status == "cancel_requested"


def test_startup_reconciliation_closes_orphaned_active_runs(repos) -> None:
    task_repo, _, run_repo = repos
    task, message = _seed_task(task_repo)
    now = time.time()
    running = ConversationRun(
        run_id="run-orphaned",
        thread_id="thread-orphaned",
        owner_id="alice",
        task_id=task.task_id,
        question=message.content,
        input_message_id=message.msg_id,
        deadline_at=now + 30,
        created_at=now,
        updated_at=now,
    )
    cancelling = running.model_copy(
        update={
            "run_id": "run-cancelling",
            "thread_id": "thread-cancelling",
            "status": "cancel_requested",
            "end_reason": "client_disconnected",
        }
    )
    run_repo.create(running)
    run_repo.create(cancelling)

    assert run_repo.reconcile_stale(now=now + 1) == 2
    failed = run_repo.get(running.run_id, "alice")
    cancelled = run_repo.get(cancelling.run_id, "alice")
    assert failed is not None and failed.status == "failed"
    assert failed.end_reason == "runtime_restarted"
    assert cancelled is not None and cancelled.status == "cancelled"
    assert cancelled.end_reason == "client_disconnected"


def test_concurrent_call_reservation_cannot_exceed_limit(repos) -> None:
    task_repo, _, run_repo = repos
    task, message = _seed_task(task_repo)
    now = time.time()
    run = ConversationRun(
        run_id="run-budget-race",
        thread_id="thread-budget-race",
        owner_id="alice",
        task_id=task.task_id,
        question=message.content,
        input_message_id=message.msg_id,
        max_tool_calls=1,
        deadline_at=now + 30,
        created_at=now,
        updated_at=now,
    )
    run_repo.create(run)
    barrier = Barrier(2)

    def reserve_once():
        barrier.wait()
        return run_repo.reserve_call(
            run.run_id,
            "alice",
            kind="tool",
            stage="tool",
            now=now + 1,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: reserve_once(), range(2)))

    assert sum(result is not None for result in results) == 1
    stored = run_repo.get(run.run_id, "alice")
    assert stored is not None and stored.tool_call_count == 1


def test_cancel_and_terminal_commit_race_has_one_consistent_outcome(repos) -> None:
    task_repo, _, run_repo = repos
    task, input_message = _seed_task(task_repo)
    now = time.time()
    run = ConversationRun(
        run_id="run-terminal-race",
        thread_id="thread-terminal-race",
        owner_id="alice",
        task_id=task.task_id,
        question=input_message.content,
        input_message_id=input_message.msg_id,
        deadline_at=now + 30,
        created_at=now,
        updated_at=now,
    )
    run_repo.create(run)
    answer = Message(
        msg_id="msg-race-answer",
        task_id=task.task_id,
        role="assistant",
        content="最终回答",
        created_at=now + 1,
    )
    completed = run.model_copy(
        update={
            "status": "completed",
            "stage": "completed",
            "answer_message_id": answer.msg_id,
            "end_reason": "answered",
            "finished_at": now + 1,
            "updated_at": now + 1,
        }
    )
    barrier = Barrier(2)

    def commit() -> str:
        barrier.wait()
        try:
            run_repo.commit_terminal(completed, answer)
        except ConversationRunConflict:
            return "lost"
        return "completed"

    def cancel() -> str:
        barrier.wait()
        result = run_repo.request_cancel(
            run.run_id,
            "alice",
            reason="user_cancelled",
            now=now + 1,
        )
        assert result is not None
        return result.status

    with ThreadPoolExecutor(max_workers=2) as executor:
        commit_result = executor.submit(commit)
        cancel_result = executor.submit(cancel)
        outcomes = {commit_result.result(), cancel_result.result()}

    stored = run_repo.get(run.run_id, "alice")
    assert stored is not None
    messages = task_repo.list_messages(task.task_id)
    if stored.status == "completed":
        assert outcomes == {"completed"}
        assert messages == [input_message, answer]
    else:
        assert stored.status == "cancel_requested"
        assert "lost" in outcomes
        assert messages == [input_message]


def test_phase1_tool_call_table_gets_additive_run_id_upgrade(tmp_path) -> None:
    path = tmp_path / "phase1.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE tool_calls (
            tool_call_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            input_json TEXT NOT NULL DEFAULT '{}',
            output_json TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            duration_ms INTEGER,
            created_at REAL NOT NULL
        )"""
    )
    connection.commit()
    connection.close()

    pool = SqliteConnectionPool(str(path))
    columns = {row[1] for row in pool.get().execute("PRAGMA table_info(tool_calls)")}
    assert "run_id" in columns


def test_existing_run_table_gets_additive_runtime_control_upgrades(tmp_path) -> None:
    path = tmp_path / "phase2.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE conversation_runs (
            run_id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL UNIQUE,
            owner_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            question TEXT NOT NULL,
            input_message_id TEXT NOT NULL,
            attachment_doc_ids TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL,
            stage TEXT NOT NULL,
            evidence_json TEXT NOT NULL DEFAULT '[]',
            verified_citation_ids TEXT NOT NULL DEFAULT '[]',
            clarification_fields TEXT NOT NULL DEFAULT '[]',
            model_call_count INTEGER NOT NULL DEFAULT 0,
            tool_call_count INTEGER NOT NULL DEFAULT 0,
            answer_message_id TEXT,
            error_type TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )"""
    )
    connection.commit()
    connection.close()

    pool = SqliteConnectionPool(str(path))
    columns = {row[1] for row in pool.get().execute("PRAGMA table_info(conversation_runs)")}
    assert {
        "verified_context_message_ids",
        "max_duration_seconds",
        "remaining_seconds",
        "deadline_at",
        "max_model_calls",
        "max_tool_calls",
        "model_retry_count",
        "tool_retry_count",
        "input_tokens",
        "output_tokens",
        "usage_reported_model_calls",
        "end_reason",
        "cancel_requested_at",
        "finished_at",
    }.issubset(columns)
