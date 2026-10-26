import sqlite3

import pytest
from langchain_core.messages import AIMessage

from domain.models import Chunk, User
from tests.fakes import FakeEvidenceQAGenerator, FakeRetrieve, FakeToolCallingModel
from tests.infra.test_conversation_checkpoint_recovery import _container, _settings


@pytest.mark.parametrize("change", ["add_during_run", "delete_before_commit"])
def test_answer_uses_only_recorded_memory_and_validates_inside_commit(tmp_path, change):
    state = {}

    class AddDuringRetrieval(FakeRetrieve):
        def retrieve(self, *args, **kwargs):
            if change == "add_during_run" and "memory" not in state:
                state["memory"] = container.account_memory.create(
                    "github:review", content="回答用简洁中文", kind="preference"
                )
            return super().retrieve(*args, **kwargs)

    model = FakeToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_law",
                        "args": {"query": "出境条件"},
                        "id": "law",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="候选"),
        ]
    )
    generator = FakeEvidenceQAGenerator()
    container = _container(
        _settings(tmp_path),
        model,
        generator=generator,
        retriever=AddDuringRetrieval(
            [
                Chunk(
                    chunk_id="law",
                    text="出境应满足法定条件。",
                    source_name="法规",
                    source_type="law",
                    score=0.9,
                )
            ]
        ),
    )
    container.user_repo.upsert(
        User(
            user_id="github:review",
            provider="github",
            provider_id="review",
            display_name="review",
            created_at=1,
            last_active_at=1,
        )
    )
    original_commit = container.conversation_run_repo.commit_terminal
    container.account_memory.set_enabled("github:review", True)
    if change == "delete_before_commit":
        state["memory"] = container.account_memory.create(
            "github:review", content="回答简洁", kind="preference"
        )

    def commit(run, message, *, validate_context=None):
        if change == "delete_before_commit":
            container.account_memory.delete("github:review", state["memory"].memory_id)

        def guard():
            assert container.pool.get().in_transaction
            other = sqlite3.connect(container.settings.sqlite_db_path, timeout=0.01)
            try:
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    other.execute("DELETE FROM account_memories")
            finally:
                other.close()
            validate_context()

        return original_commit(run, message, validate_context=guard)

    container.conversation_run_repo.commit_terminal = commit
    try:
        stream = container.run_copilot.stream(
            owner_id="github:review", task_id=None, user_message="出境条件是什么？"
        )
        events = list(stream)
        run = container.conversation_run_repo.get(stream.run_id, "github:review")
        if change == "add_during_run":
            assert generator.calls[0]["preferences"] == []
            assert container.account_memory_repo.references(stream.run_id, "github:review") == {}
            assert run.status == "completed"
        else:
            assert generator.calls[0]["preferences"] == ["回答简洁"]
            assert run.status == "failed"
            assert all(event.event_type.value != "answer" for event in events)
            assert all(
                m.role != "assistant" for m in container.task_repo.list_messages(run.task_id)
            )
    finally:
        container.close()
