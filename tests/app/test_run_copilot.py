"""Conversation orchestration, attachment selection and run identity tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from api.v2.sse import stream_with_keepalive
from app.use_cases.run_copilot import RunCopilotUseCase
from app.use_cases.task_management import TaskManagementUseCase
from domain.agent import AgentEventType
from domain.errors import DocumentNotFound, TaskNotFound
from tests.fakes import FakeCopilotAgent, FakeTrace, InMemoryConversationRunRepo, InMemoryTaskRepo


class ReadyDocuments:
    def __init__(self, owners: dict[str, str] | None = None) -> None:
        self.owners = owners or {}

    def get_detail(self, document_id: str, owner_id: str):
        if self.owners.get(document_id, owner_id) != owner_id:
            from domain.errors import DocumentNotFound

            raise DocumentNotFound(document_id)
        return SimpleNamespace(document=SimpleNamespace(status="ready"))


def _make_uc(*, documents=None, scheduler=None, trace=None):
    task_repo = InMemoryTaskRepo()
    run_repo = InMemoryConversationRunRepo(task_repo)
    agent = FakeCopilotAgent()
    return (
        RunCopilotUseCase(
            agent=agent,
            task_management=TaskManagementUseCase(task_repo),
            run_repo=run_repo,
            documents=documents,
            memory_scheduler=scheduler,
            trace=trace,
        ),
        agent,
        task_repo,
        run_repo,
    )


def test_new_task_creates_server_owned_run_and_persists_user_message() -> None:
    use_case, agent, task_repo, run_repo = _make_uc()

    events = list(use_case.stream(owner_id="anon:alice", task_id=None, user_message="你好？"))

    assert [event.event_type for event in events] == [
        AgentEventType.TASK_CREATED,
        AgentEventType.RUN_STARTED,
        AgentEventType.ANSWER,
    ]
    task_id = events[0].payload["task_id"]
    run_id = events[1].payload["run_id"]
    run = run_repo.get(run_id, "anon:alice")
    assert run is not None
    assert run.task_id == task_id
    assert run.thread_id.startswith("thread_")
    assert len(task_repo.list_messages(task_id)) == 1
    assert agent.calls[0]["run_id"] == run_id


def test_existing_task_does_not_emit_task_created() -> None:
    use_case, _, task_repo, _ = _make_uc()
    task = TaskManagementUseCase(task_repo).create_task("anon:alice", title="existing")

    events = list(
        use_case.stream(owner_id="anon:alice", task_id=task.task_id, user_message="你好？")
    )

    assert events[0].event_type is AgentEventType.RUN_STARTED
    assert all(event.event_type is not AgentEventType.TASK_CREATED for event in events)


def test_attachment_replace_inherit_and_clear_semantics() -> None:
    documents = ReadyDocuments()
    use_case, agent, task_repo, _ = _make_uc(documents=documents)

    first = list(
        use_case.stream(
            owner_id="anon:alice",
            task_id=None,
            user_message="分析附件",
            attachment_doc_ids=["doc-a", "doc-b", "doc-a"],
        )
    )
    task_id = first[0].payload["task_id"]
    assert task_repo.list_attachment_ids(task_id, "anon:alice") == ["doc-a", "doc-b"]
    assert agent.calls[-1]["attachment_doc_ids"] == ["doc-a", "doc-b"]

    list(use_case.stream(owner_id="anon:alice", task_id=task_id, user_message="继续"))
    assert agent.calls[-1]["attachment_doc_ids"] == ["doc-a", "doc-b"]

    list(
        use_case.stream(
            owner_id="anon:alice",
            task_id=task_id,
            user_message="清除附件后继续",
            attachment_doc_ids=[],
        )
    )
    assert task_repo.list_attachment_ids(task_id, "anon:alice") == []
    assert agent.calls[-1]["attachment_doc_ids"] == []


def test_attachment_owner_is_checked_before_selection_is_saved() -> None:
    use_case, _, task_repo, _ = _make_uc(documents=ReadyDocuments({"private": "anon:alice"}))
    task = TaskManagementUseCase(task_repo).create_task("anon:bob")

    with pytest.raises(DocumentNotFound):
        list(
            use_case.stream(
                owner_id="anon:bob",
                task_id=task.task_id,
                user_message="读取材料",
                attachment_doc_ids=["private"],
            )
        )
    assert task_repo.list_attachment_ids(task.task_id, "anon:bob") == []


def test_task_and_attachment_selection_do_not_cross_owner() -> None:
    use_case, _, task_repo, _ = _make_uc(documents=ReadyDocuments())
    task = TaskManagementUseCase(task_repo).create_task("anon:alice")
    task_repo.set_attachment_ids(task.task_id, "anon:alice", ["doc-a"])

    with pytest.raises(TaskNotFound):
        list(use_case.stream(owner_id="anon:bob", task_id=task.task_id, user_message="继续"))


class RecordingScheduler:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail = fail

    def schedule_summarization(self, owner_id: str, task_id: str) -> None:
        if self.fail:
            raise RuntimeError("summary unavailable")
        self.calls.append((owner_id, task_id))


@pytest.mark.parametrize("fail", [False, True])
def test_summary_scheduling_is_best_effort(fail: bool) -> None:
    scheduler = RecordingScheduler(fail=fail)
    use_case, _, _, _ = _make_uc(scheduler=scheduler)
    events = list(use_case.stream(owner_id="anon:alice", task_id=None, user_message="你好？"))
    assert events[-1].event_type is AgentEventType.ANSWER
    if not fail:
        assert len(scheduler.calls) == 1


def test_validation_rejects_blank_owner_or_message() -> None:
    use_case, _, _, _ = _make_uc()
    with pytest.raises(ValueError, match="owner_id"):
        list(use_case.stream(owner_id="", task_id=None, user_message="q"))
    with pytest.raises(ValueError, match="user_message"):
        list(use_case.stream(owner_id="anon:alice", task_id=None, user_message=""))


def test_cancel_before_first_event_is_terminal_and_idempotent() -> None:
    use_case, agent, _, run_repo = _make_uc()
    events = use_case.stream(owner_id="anon:alice", task_id=None, user_message="问题")
    run_id = events.run_id  # type: ignore[attr-defined]

    first = use_case.cancel(owner_id="anon:alice", run_id=run_id)
    second = use_case.cancel(owner_id="anon:alice", run_id=run_id)

    assert first.status == "cancelled"
    assert first.end_reason == "user_cancelled"
    assert second == first
    assert agent.calls == []
    emitted = list(events)
    assert emitted[-1].event_type is AgentEventType.RUN_STATUS
    assert emitted[-1].payload["status"] == "cancelled"
    assert run_repo.get(run_id, "anon:alice").status == "cancelled"


def test_cancel_rejects_other_owner() -> None:
    use_case, _, _, _ = _make_uc()
    events = use_case.stream(owner_id="anon:alice", task_id=None, user_message="问题")

    with pytest.raises(TaskNotFound):
        use_case.cancel(owner_id="anon:bob", run_id=events.run_id)  # type: ignore[attr-defined]


def test_cancel_request_is_linked_to_run_trace_without_content() -> None:
    trace = FakeTrace()
    use_case, _, _, _ = _make_uc(trace=trace)
    events = use_case.stream(owner_id="anon:alice", task_id=None, user_message="敏感内容")

    use_case.cancel(
        owner_id="anon:alice",
        run_id=events.run_id,  # type: ignore[attr-defined]
    )

    cancel_span = trace.spans[-1]
    assert cancel_span["name"] == "riskpilot.copilot.cancel"
    assert cancel_span["metadata"]["status"] == "cancelled"
    assert "敏感内容" not in str(cancel_span)


def test_stream_disconnect_then_repeated_close_finishes_run_as_cancelled_once() -> None:
    use_case, agent, _, run_repo = _make_uc()
    events = use_case.stream(owner_id="anon:alice", task_id=None, user_message="问题")
    run_id = events.run_id  # type: ignore[attr-defined]

    async def disconnect_after_first_frame() -> None:
        stream = stream_with_keepalive(events, keepalive_seconds=0.5)
        assert "event: task_created" in await anext(stream)
        await stream.aclose()
        await stream.aclose()

    asyncio.run(disconnect_after_first_frame())

    stored = run_repo.get(run_id, "anon:alice")
    assert stored is not None and stored.status == "cancelled"
    assert stored.end_reason == "client_disconnected"
    assert agent.calls == []
