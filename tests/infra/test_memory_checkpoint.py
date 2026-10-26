"""Deleted account memory cannot be revived through a persisted LangGraph thread."""

import time

import pytest
from langchain_core.messages import AIMessage

from domain.models import User
from tests.fakes import FakeToolCallingModel
from tests.infra.test_conversation_checkpoint_recovery import _container, _settings


@pytest.mark.parametrize("mutation", ["delete", "update"])
def test_changed_memory_blocks_real_checkpoint_resume(tmp_path, mutation):
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
                            "id": "law",
                            "type": "tool_call",
                        },
                    ],
                )
            ]
        ),
        interrupt_after=["tools"],
    )
    owner = "github:memory-owner"
    first.user_repo.upsert(
        User(
            user_id=owner,
            provider="github",
            provider_id="memory-owner",
            display_name="Memory Owner",
            created_at=time.time(),
            last_active_at=time.time(),
        )
    )
    try:
        first.account_memory.set_enabled(owner, True)
        memory = first.account_memory.create(owner, content="回答优先给出依据", kind="preference")
        stream = first.run_copilot.stream(
            owner_id=owner, task_id=None, user_message="查询个人信息出境条件"
        )
        list(stream)
        run_id = stream.run_id
        assert first.conversation_run_repo.get(run_id, owner).status == "interrupted"
        assert memory.memory_id in first.account_memory_repo.references(run_id, owner)
        if mutation == "delete":
            first.account_memory.delete(owner, memory.memory_id)
        else:
            list(
                first.run_copilot.stream(
                    owner_id=owner, task_id=None, user_message="请更新这条偏好：回答详细解释"
                )
            )
    finally:
        first.close()
    second = _container(settings, FakeToolCallingModel(responses=[AIMessage(content="不应执行")]))
    try:
        with pytest.raises(ValueError, match="记忆已"):
            second.run_copilot.resume(owner_id=owner, run_id=run_id)
        run = second.conversation_run_repo.get(run_id, owner)
        assert run.status == "cancelled"
        assert run.end_reason == "memory_context_changed"
        assert run.answer_message_id is None
    finally:
        second.close()
