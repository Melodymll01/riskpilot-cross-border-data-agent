"""Rebuild the LangGraph runtime and resume one interrupted conversation run."""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk

from api.v2.copilot import build_copilot_routes
from api.v2.errors import install_exception_handlers
from app.container import AppContainer
from config import Settings
from domain.agent import AgentEventType
from domain.errors import ConversationRunConflict, TaskNotFound
from domain.models import Chunk
from tests.fakes import (
    FakeAuth,
    FakeChat,
    FakeClaimSupportVerifier,
    FakeDocumentLoader,
    FakeDocumentParser,
    FakeEmbed,
    FakeEvidenceQAGenerator,
    FakeJobDispatcher,
    FakeKbRepo,
    FakeObjectStore,
    FakeReadiness,
    FakeRetrieve,
    FakeStreamingToolCallingModel,
    FakeToolCallingModel,
    FakeWebSearch,
)


def _settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "llm_provider": "local",
        "embed_provider": "local",
        "sqlite_db_path": str(tmp_path / "app.sqlite3"),
        "langgraph_checkpoint_db_path": str(tmp_path / "checkpoints.sqlite3"),
        "object_store_dir": str(tmp_path / "objects"),
        "memory_enabled": False,
        "enable_reranker": False,
    }
    values.update(overrides)
    return Settings(
        _env_file=None,
        **values,
    )


def _container(
    settings: Settings,
    model: FakeToolCallingModel,
    *,
    interrupt_after: list[str] | None = None,
    retriever=None,
    generator=None,
    verifier=None,
) -> AppContainer:
    law = Chunk(
        chunk_id="law-38",
        text="个人信息保护法第三十八条规定了个人信息出境条件。",
        source_type="law",
        source_name="中华人民共和国个人信息保护法",
        title="第三十八条",
        source_url="https://example.test/pipl",
        score=0.9,
    )
    return AppContainer(
        settings,
        embedder=FakeEmbed(),
        chat=FakeChat(),
        retriever=retriever or FakeRetrieve([law]),
        web_search=FakeWebSearch(),
        kb_repo=FakeKbRepo(),
        document_loader=FakeDocumentLoader(),
        object_store=FakeObjectStore(),
        document_parser=FakeDocumentParser(),
        evidence_qa_generator=generator or FakeEvidenceQAGenerator(),
        claim_support_verifier=verifier or FakeClaimSupportVerifier(),
        auth=FakeAuth(),
        job_dispatcher=FakeJobDispatcher(),
        readiness=FakeReadiness(),
        agent_model=model,
        agent_interrupt_after=interrupt_after,
    )


def _copilot_api(container: AppContainer) -> FastAPI:
    app = FastAPI()
    app.state.container = container
    app.include_router(build_copilot_routes(container), prefix="/api/v2")
    install_exception_handlers(app)
    return app


def _sse_payload(body: str, event_type: str) -> dict[str, object]:
    for block in body.split("\n\n"):
        lines = block.splitlines()
        if f"event: {event_type}" not in lines:
            continue
        data = next(line.removeprefix("data: ") for line in lines if line.startswith("data: "))
        return json.loads(data)
    raise AssertionError(f"missing SSE event: {event_type}")


def test_rebuild_runtime_resumes_without_duplicate_messages_or_tools(tmp_path: Path) -> None:
    first_model = FakeToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_law",
                        "args": {"query": "第三十八条"},
                        "id": "call-law",
                        "type": "tool_call",
                    }
                ],
            )
        ]
    )
    settings = _settings(tmp_path)
    first = _container(settings, first_model, interrupt_after=["tools"])
    try:
        events = list(
            first.run_copilot.stream(
                owner_id="anon:alice",
                task_id=None,
                user_message="个人信息出境有什么条件？",
            )
        )
        task_id = events[0].payload["task_id"]
        run_id = events[1].payload["run_id"]
        assert events[-1].event_type is AgentEventType.RUN_INTERRUPTED
        run = first.conversation_run_repo.get(run_id, "anon:alice")
        assert run is not None and run.status == "interrupted"
        assert len(run.evidence) == 1
        assert run.model_call_count == 1
        assert run.tool_call_count == 1
        assert 0 < run.remaining_seconds < run.max_duration_seconds
        remaining_after_interrupt = run.remaining_seconds
    finally:
        first.close()

    second = _container(
        settings,
        FakeToolCallingModel(responses=[AIMessage(content="恢复后的候选回答")]),
    )
    try:
        resumed = list(second.run_copilot.resume(owner_id="anon:alice", run_id=run_id))
        assert resumed[0].event_type is AgentEventType.RUN_RESUMED
        assert resumed[-1].event_type is AgentEventType.ANSWER
        assert resumed[-1].payload["citations"][0]["source_id"] == "law-38"
        run = second.conversation_run_repo.get(run_id, "anon:alice")
        assert run is not None and run.status == "completed"
        assert run.stage == "completed"
        # Agent 决策 2 次 + Claim 生成 1 次 + 语义验证 1 次。
        assert run.model_call_count == 4
        assert run.tool_call_count == 1
        assert run.remaining_seconds <= remaining_after_interrupt
        assert run.verified_citation_ids == [resumed[-1].payload["citations"][0]["citation_id"]]
        assert len(second.task_repo.list_messages(task_id)) == 2
        tool_count = (
            second.pool.get()
            .execute("SELECT COUNT(*) FROM tool_calls WHERE task_id = ?", (task_id,))
            .fetchone()[0]
        )
        assert tool_count == 1
        persisted_tool_run = (
            second.pool.get()
            .execute("SELECT run_id FROM tool_calls WHERE task_id = ?", (task_id,))
            .fetchone()["run_id"]
        )
        assert persisted_tool_run == run_id
        with pytest.raises(TaskNotFound):
            list(second.run_copilot.resume(owner_id="anon:bob", run_id=run_id))
    finally:
        second.close()


def test_sse_normal_eof_preserves_interrupted_run_and_http_resume_succeeds(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    first = _container(
        settings,
        FakeToolCallingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "search_law",
                            "args": {"query": "第三十八条"},
                            "id": "call-law",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        ),
        interrupt_after=["tools"],
    )
    try:
        with TestClient(_copilot_api(first)) as client:
            client.cookies.set(settings.cookie_name, first.auth.issue_jwt("anon:alice"))
            with client.stream(
                "POST",
                "/api/v2/copilot/chat/stream",
                json={"message": "个人信息出境有什么条件？"},
            ) as response:
                assert response.status_code == 200
                body = response.read().decode("utf-8")
        run_id = str(_sse_payload(body, "run_started")["run_id"])
        assert _sse_payload(body, "run_interrupted")["run_id"] == run_id
        paused = first.conversation_run_repo.get(run_id, "anon:alice")
        assert paused is not None and paused.status == "interrupted"
    finally:
        first.close()

    second = _container(
        settings,
        FakeToolCallingModel(responses=[AIMessage(content="恢复后的候选回答")]),
    )
    try:
        with TestClient(_copilot_api(second)) as client:
            client.cookies.set(settings.cookie_name, second.auth.issue_jwt("anon:alice"))
            with client.stream(
                "POST",
                f"/api/v2/copilot/runs/{run_id}/resume",
            ) as response:
                assert response.status_code == 200
                resumed_body = response.read().decode("utf-8")
        assert _sse_payload(resumed_body, "run_resumed")["run_id"] == run_id
        assert _sse_payload(resumed_body, "answer")["msg_id"]
        completed = second.conversation_run_repo.get(run_id, "anon:alice")
        assert completed is not None and completed.status == "completed"
        assert len(second.task_repo.list_messages(completed.task_id)) == 2
    finally:
        second.close()


def test_user_cancel_of_interrupted_run_is_idempotent_and_blocks_resume(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    container = _container(
        settings,
        FakeToolCallingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "search_law",
                            "args": {"query": "第三十八条"},
                            "id": "call-law",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        ),
        interrupt_after=["tools"],
    )
    try:
        with TestClient(_copilot_api(container)) as client:
            client.cookies.set(settings.cookie_name, container.auth.issue_jwt("anon:alice"))
            response = client.post(
                "/api/v2/copilot/chat/stream",
                json={"message": "个人信息出境有什么条件？"},
            )
            assert response.status_code == 200
            run_id = str(_sse_payload(response.text, "run_started")["run_id"])
            assert container.conversation_run_repo.get(run_id, "anon:alice").status == (
                "interrupted"
            )

            first_cancel = client.post(f"/api/v2/copilot/runs/{run_id}/cancel")
            second_cancel = client.post(f"/api/v2/copilot/runs/{run_id}/cancel")
            resume = client.post(f"/api/v2/copilot/runs/{run_id}/resume")

        assert first_cancel.status_code == 200
        assert first_cancel.json()["status"] == "cancelled"
        assert first_cancel.json()["end_reason"] == "user_cancelled"
        assert second_cancel.status_code == 200
        assert second_cancel.json()["status"] == "cancelled"
        assert resume.status_code == 409
        assert len(container.task_repo.list_messages(first_cancel.json()["task_id"])) == 1
    finally:
        container.close()


def test_interleaved_resume_requests_have_single_executor(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = _container(
        settings,
        FakeToolCallingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "search_law",
                            "args": {"query": "第三十八条"},
                            "id": "call-law",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        ),
        interrupt_after=["tools"],
    )
    try:
        initial = list(
            first.run_copilot.stream(
                owner_id="anon:alice",
                task_id=None,
                user_message="个人信息出境有什么条件？",
            )
        )
        task_id = initial[0].payload["task_id"]
        run_id = initial[1].payload["run_id"]
    finally:
        first.close()

    second = _container(
        settings,
        FakeToolCallingModel(responses=[AIMessage(content="恢复后的候选回答")]),
    )
    try:
        first_resume = second.run_copilot.resume(owner_id="anon:alice", run_id=run_id)
        with pytest.raises(ConversationRunConflict):
            second.run_copilot.resume(owner_id="anon:alice", run_id=run_id)
        first_event = next(first_resume)
        assert first_event.event_type is AgentEventType.RUN_RESUMED
        first_events = [*first_resume]
        answer_count = sum(
            event.event_type is AgentEventType.ANSWER for event in [first_event, *first_events]
        )
        assert answer_count == 1
        assert len(second.task_repo.list_messages(task_id)) == 2
        with pytest.raises(ConversationRunConflict):
            second.run_copilot.resume(owner_id="anon:alice", run_id=run_id)
        assert len(second.task_repo.list_messages(task_id)) == 2
    finally:
        second.close()


def test_concurrent_resume_requests_have_single_sqlite_claim(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = _container(
        settings,
        FakeToolCallingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "search_law",
                            "args": {"query": "第三十八条"},
                            "id": "call-law",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        ),
        interrupt_after=["tools"],
    )
    try:
        initial = list(
            first.run_copilot.stream(
                owner_id="anon:alice",
                task_id=None,
                user_message="个人信息出境有什么条件？",
            )
        )
        run_id = initial[1].payload["run_id"]
    finally:
        first.close()

    second = _container(
        settings,
        FakeToolCallingModel(responses=[AIMessage(content="恢复后的候选回答")]),
    )
    barrier = threading.Barrier(2)

    def claim():
        barrier.wait()
        try:
            return second.run_copilot.resume(owner_id="anon:alice", run_id=run_id)
        except ConversationRunConflict:
            return None

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: claim(), range(2)))
        winners = [result for result in results if result is not None]
        assert len(winners) == 1
        resumed = list(winners[0])
        assert resumed[0].event_type is AgentEventType.RUN_RESUMED
        assert resumed[-1].event_type is AgentEventType.ANSWER
    finally:
        second.close()


def test_terminal_commit_failure_rolls_back_message_and_marks_run_failed(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = _container(
        settings,
        FakeToolCallingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "search_law",
                            "args": {"query": "第三十八条"},
                            "id": "call-law",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        ),
        interrupt_after=["tools"],
    )
    try:
        initial = list(
            first.run_copilot.stream(
                owner_id="anon:alice",
                task_id=None,
                user_message="个人信息出境有什么条件？",
            )
        )
        task_id = initial[0].payload["task_id"]
        run_id = initial[1].payload["run_id"]
    finally:
        first.close()

    second = _container(
        settings,
        FakeToolCallingModel(responses=[AIMessage(content="恢复后的候选回答")]),
    )
    try:
        connection = second.pool.get()
        connection.execute(
            """CREATE TRIGGER reject_terminal_commit
            BEFORE UPDATE OF answer_message_id ON conversation_runs
            WHEN NEW.answer_message_id IS NOT NULL
            BEGIN
                SELECT RAISE(ABORT, 'terminal commit rejected');
            END"""
        )
        connection.commit()

        resumed = list(second.run_copilot.resume(owner_id="anon:alice", run_id=run_id))

        assert resumed[0].event_type is AgentEventType.RUN_RESUMED
        assert any(event.event_type is AgentEventType.TOOL_ERROR for event in resumed)
        assert resumed[-1].event_type is AgentEventType.RUN_STATUS
        assert resumed[-1].payload["status"] == "failed"
        assert all(event.event_type is not AgentEventType.ANSWER for event in resumed)
        assert len(second.task_repo.list_messages(task_id)) == 1
        run = second.conversation_run_repo.get(run_id, "anon:alice")
        assert run is not None and run.status == "failed"
        assert run.answer_message_id is None
        assert run.error_type == "TerminalCommitIntegrityError"
    finally:
        second.close()


def test_abandoned_resume_does_not_leave_run_running(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = _container(
        settings,
        FakeToolCallingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "search_law",
                            "args": {"query": "第三十八条"},
                            "id": "call-law",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        ),
        interrupt_after=["tools"],
    )
    try:
        initial = list(
            first.run_copilot.stream(
                owner_id="anon:alice",
                task_id=None,
                user_message="个人信息出境有什么条件？",
            )
        )
        run_id = initial[1].payload["run_id"]
    finally:
        first.close()

    second = _container(
        settings,
        FakeToolCallingModel(responses=[AIMessage(content="不会执行")]),
    )
    try:
        resumed = second.run_copilot.resume(owner_id="anon:alice", run_id=run_id)
        assert next(resumed).event_type is AgentEventType.RUN_RESUMED
        resumed.disconnect()
        resumed.close()

        run = second.conversation_run_repo.get(run_id, "anon:alice")
        assert run is not None and run.status == "cancelled"
        assert run.end_reason == "client_disconnected"
    finally:
        second.close()


def test_cancel_after_resume_claim_but_before_first_event(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = _container(
        settings,
        FakeToolCallingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "search_law",
                            "args": {"query": "第三十八条"},
                            "id": "call-law",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        ),
        interrupt_after=["tools"],
    )
    try:
        initial = list(
            first.run_copilot.stream(
                owner_id="anon:alice",
                task_id=None,
                user_message="个人信息出境有什么条件？",
            )
        )
        run_id = initial[1].payload["run_id"]
    finally:
        first.close()

    second_model = FakeToolCallingModel(responses=[AIMessage(content="不应执行")])
    second = _container(settings, second_model)
    try:
        resumed = second.run_copilot.resume(owner_id="anon:alice", run_id=run_id)
        cancelled = second.run_copilot.cancel(owner_id="anon:alice", run_id=run_id)
        assert cancelled.status == "cancelled"
        events = list(resumed)
        assert events[0].event_type is AgentEventType.RUN_RESUMED
        assert events[-1].event_type is AgentEventType.RUN_STATUS
        assert events[-1].payload["status"] == "cancelled"
        assert second_model.calls == []
    finally:
        second.close()


def test_cancel_during_model_stops_before_tools_or_verification(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()
    model = FakeStreamingToolCallingModel(
        responses=[
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "search_law",
                            "args": {"query": "不应执行"},
                            "id": "call-never",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        ],
        pause_call_index=0,
        pause_before_first_chunk=True,
        call_started_event=started,
        release_event=release,
    )
    retriever = FakeRetrieve()
    settings = _settings(tmp_path)
    container = _container(settings, model, retriever=retriever)
    stream = container.run_copilot.stream(
        owner_id="anon:alice",
        task_id=None,
        user_message="需要检索的问题",
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(list, stream)
            assert started.wait(timeout=2.0)
            requested = container.run_copilot.cancel(
                owner_id="anon:alice",
                run_id=stream.run_id,  # type: ignore[attr-defined]
            )
            assert requested.status == "cancel_requested"
            events = result.result(timeout=2.0)
            release.set()

        assert events[-1].event_type is AgentEventType.RUN_STATUS
        assert events[-1].payload["status"] == "cancelled"
        assert retriever.calls == []
        assert container.evidence_qa_generator.calls == []
        assert len(container.task_repo.list_messages(events[0].payload["task_id"])) == 1
    finally:
        release.set()
        container.close()


def test_cancel_during_tool_discards_late_result(tmp_path: Path) -> None:
    class SlowRetrieve(FakeRetrieve):
        def __init__(self) -> None:
            super().__init__()
            self.started = threading.Event()
            self.release = threading.Event()

        def retrieve(self, *args, **kwargs):
            self.calls.append({"args": args, "kwargs": kwargs})
            self.started.set()
            self.release.wait(timeout=5.0)
            return []

    slow = SlowRetrieve()
    settings = _settings(tmp_path)
    container = _container(
        settings,
        FakeToolCallingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "search_law",
                            "args": {"query": "第三十八条"},
                            "id": "call-law",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="迟到候选"),
            ]
        ),
        retriever=slow,
    )
    stream = container.run_copilot.stream(
        owner_id="anon:alice",
        task_id=None,
        user_message="需要检索的问题",
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(list, stream)
            assert slow.started.wait(timeout=2.0)
            container.run_copilot.cancel(
                owner_id="anon:alice",
                run_id=stream.run_id,  # type: ignore[attr-defined]
            )
            events = result.result(timeout=2.0)
            slow.release.set()
            time.sleep(0.05)

        assert events[-1].payload["status"] == "cancelled"
        assert all(event.event_type is not AgentEventType.ANSWER for event in events)
        assert container.evidence_qa_generator.calls == []
        task_id = events[0].payload["task_id"]
        assert len(container.task_repo.list_messages(task_id)) == 1
        run = container.conversation_run_repo.get(
            stream.run_id,  # type: ignore[attr-defined]
            "anon:alice",
        )
        assert run is not None and run.tool_call_count == 1
    finally:
        slow.release.set()
        container.close()


def test_tool_timeout_discards_late_result(tmp_path: Path) -> None:
    class SlowRetrieve(FakeRetrieve):
        def __init__(self) -> None:
            super().__init__()
            self.release = threading.Event()

        def retrieve(self, *args, **kwargs):
            self.calls.append({"args": args, "kwargs": kwargs})
            self.release.wait(timeout=5.0)
            return []

    slow = SlowRetrieve()
    settings = _settings(
        tmp_path,
        conversation_tool_call_timeout_seconds=0.1,
        conversation_control_poll_seconds=0.01,
    )
    container = _container(
        settings,
        FakeToolCallingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "search_law",
                            "args": {"query": "第三十八条"},
                            "id": "call-law",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        ),
        retriever=slow,
    )
    stream = container.run_copilot.stream(
        owner_id="anon:alice",
        task_id=None,
        user_message="慢工具",
    )
    try:
        events = list(stream)
        slow.release.set()
        time.sleep(0.05)
        assert events[-1].event_type is AgentEventType.RUN_STATUS
        assert events[-1].payload["status"] == "timed_out"
        assert all(event.event_type is not AgentEventType.ANSWER for event in events)
        run = container.conversation_run_repo.get(
            stream.run_id,  # type: ignore[attr-defined]
            "anon:alice",
        )
        assert run is not None and run.end_reason == "search_law_timeout"
    finally:
        slow.release.set()
        container.close()


def test_model_budget_exhaustion_does_not_publish_answer(tmp_path: Path) -> None:
    settings = _settings(tmp_path, conversation_max_model_calls=1)
    container = _container(
        settings,
        FakeToolCallingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "search_law",
                            "args": {"query": "第三十八条"},
                            "id": "call-law",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="不应到达"),
            ]
        ),
    )
    try:
        events = list(
            container.run_copilot.stream(
                owner_id="anon:alice",
                task_id=None,
                user_message="个人信息出境有什么条件？",
            )
        )
        assert events[-1].event_type is AgentEventType.RUN_STATUS
        assert events[-1].payload["status"] == "budget_exhausted"
        assert all(event.event_type is not AgentEventType.ANSWER for event in events)
        run_id = events[1].payload["run_id"]
        run = container.conversation_run_repo.get(run_id, "anon:alice")
        assert run is not None
        assert run.model_call_count == 1
        assert run.end_reason == "model_call_limit"
    finally:
        container.close()


def test_tool_budget_exhaustion_stops_before_second_tool(tmp_path: Path) -> None:
    settings = _settings(tmp_path, conversation_max_tool_calls=1)
    model = FakeToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_law",
                        "args": {"query": "第三十八条"},
                        "id": "call-law-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_law",
                        "args": {"query": "第四十条"},
                        "id": "call-law-2",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="不应到达"),
        ]
    )
    retriever = FakeRetrieve(
        [
            Chunk(
                chunk_id="law-38",
                text="法规证据",
                source_type="law",
                source_name="法规",
                score=0.9,
            )
        ]
    )
    container = _container(settings, model, retriever=retriever)
    try:
        events = list(
            container.run_copilot.stream(
                owner_id="anon:alice",
                task_id=None,
                user_message="连续检索",
            )
        )
        assert events[-1].event_type is AgentEventType.RUN_STATUS
        assert events[-1].payload["status"] == "budget_exhausted"
        assert len(retriever.calls) == 1
        run_id = events[1].payload["run_id"]
        run = container.conversation_run_repo.get(run_id, "anon:alice")
        assert run is not None and run.tool_call_count == 1
        assert run.end_reason == "tool_call_limit"
    finally:
        container.close()


def test_overall_timeout_during_model_has_no_final_message(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()
    model = FakeStreamingToolCallingModel(
        responses=[[AIMessageChunk(content="迟到正文")]],
        pause_call_index=0,
        pause_before_first_chunk=True,
        call_started_event=started,
        release_event=release,
    )
    settings = _settings(
        tmp_path,
        conversation_run_timeout_seconds=0.05,
        conversation_model_call_timeout_seconds=1.0,
        conversation_control_poll_seconds=0.01,
    )
    container = _container(settings, model)
    stream = container.run_copilot.stream(
        owner_id="anon:alice",
        task_id=None,
        user_message="会超时的问题",
    )
    try:
        events = list(stream)
        release.set()
        assert started.is_set()
        assert events[-1].event_type is AgentEventType.RUN_STATUS
        assert events[-1].payload["status"] == "timed_out"
        assert all(event.event_type is not AgentEventType.ANSWER for event in events)
        run = container.conversation_run_repo.get(
            stream.run_id,  # type: ignore[attr-defined]
            "anon:alice",
        )
        assert run is not None and run.end_reason == "run_timeout"
    finally:
        release.set()
        container.close()


def test_verification_timeout_discards_streamed_draft(tmp_path: Path) -> None:
    verifier_started = threading.Event()
    verifier_release = threading.Event()
    verifier = FakeClaimSupportVerifier(
        started_event=verifier_started,
        release_event=verifier_release,
    )
    model = FakeStreamingToolCallingModel(
        responses=[
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "search_law",
                            "args": {"query": "第三十八条"},
                            "id": "call-law",
                            "type": "tool_call",
                        }
                    ],
                )
            ],
            [AIMessageChunk(content="这是一段尚未核验的草稿")],
        ]
    )
    settings = _settings(
        tmp_path,
        conversation_model_call_timeout_seconds=0.1,
        conversation_control_poll_seconds=0.01,
    )
    container = _container(settings, model, verifier=verifier)
    stream = container.run_copilot.stream(
        owner_id="anon:alice",
        task_id=None,
        user_message="个人信息出境有什么条件？",
    )
    try:
        events = list(stream)
        verifier_release.set()
        time.sleep(0.05)
        assert verifier_started.is_set()
        assert any(event.event_type is AgentEventType.DRAFT_DELTA for event in events)
        assert any(event.event_type is AgentEventType.DRAFT_DISCARDED for event in events)
        assert events[-1].event_type is AgentEventType.RUN_STATUS
        assert events[-1].payload["status"] == "timed_out"
        assert all(event.event_type is not AgentEventType.ANSWER for event in events)
        run = container.conversation_run_repo.get(
            stream.run_id,  # type: ignore[attr-defined]
            "anon:alice",
        )
        assert run is not None and run.end_reason == "verification_timeout"
    finally:
        verifier_release.set()
        container.close()
