"""Graph event consumption must not replace per-operation deadlines."""

import time

import pytest
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk

from domain.errors import ConversationRunStopped
from tests.app.test_runtime_control import _control, _run
from tests.fakes import FakeStreamingToolCallingModel, InMemoryConversationRunRepo, InMemoryTaskRepo
from tests.infra.test_conversation_checkpoint_recovery import _container, _settings


def test_nested_tool_can_exceed_one_model_timeout():
    repo = InMemoryConversationRunRepo(InMemoryTaskRepo())
    run = _run()
    repo.create(run)
    control = _control(repo, run, model=0.5, tool=2)

    def risk():
        for operation in ["risk_analysis", "verification"]:
            control.call_model(
                operation=operation, stage="verification", invoke=lambda: time.sleep(0.3)
            )
        return "verified"

    try:
        assert (
            control.wait_for_graph_event(
                lambda: control.call_tool(operation="assess_risk", invoke=risk)
            )
            == "verified"
        )
        stored = repo.get(run.run_id, run.owner_id)
        assert stored.status == "running" and stored.model_call_count == 2
        assert stored.tool_call_count == 1
    finally:
        control.close()


def test_tool_total_timeout_keeps_its_own_reason():
    repo = InMemoryConversationRunRepo(InMemoryTaskRepo())
    run = _run()
    repo.create(run)
    control = _control(repo, run, model=1, tool=0.2)
    try:
        with pytest.raises(ConversationRunStopped) as raised:
            control.wait_for_graph_event(
                lambda: control.call_tool(operation="search_law", invoke=lambda: time.sleep(0.4))
            )
        assert raised.value.reason == "search_law_timeout"
    finally:
        control.close()


class SlowChunkModel(FakeStreamingToolCallingModel):
    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        for _ in range(8):
            time.sleep(0.1)
            yield ChatGenerationChunk(message=AIMessageChunk(content="正文"))


def test_stream_chunks_do_not_reset_model_deadline(tmp_path):
    model = SlowChunkModel(responses=[])
    container = _container(
        _settings(
            tmp_path,
            conversation_model_call_timeout_seconds=0.4,
            conversation_run_timeout_seconds=10,
        ),
        model,
    )
    try:
        stream = container.run_copilot.stream(
            owner_id="anon:alice", task_id=None, user_message="分析数据出境"
        )
        events = list(stream)
        run = container.conversation_run_repo.get(stream.run_id, "anon:alice")
        assert run.status == "timed_out"
        assert run.end_reason == "agent_model_timeout"
        assert all(event.event_type.value != "answer" for event in events)
    finally:
        container.close()
