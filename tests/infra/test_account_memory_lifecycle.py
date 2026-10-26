"""Exercise memory commands and Agent recall across real persisted conversations."""

import sqlite3

import pytest
from langchain_core.messages import AIMessage

from domain.models import User
from tests.fakes import FakeToolCallingModel
from tests.infra.test_conversation_checkpoint_recovery import _container, _settings


def _account(container, owner):
    container.user_repo.upsert(
        User(
            user_id=owner,
            provider="github",
            provider_id=owner,
            display_name=owner,
            created_at=1,
            last_active_at=1,
        )
    )
    container.account_memory.set_enabled(owner, True)


def _turn(container, owner, text):
    stream = container.run_copilot.stream(owner_id=owner, task_id=None, user_message=text)
    events = list(stream)
    run = container.conversation_run_repo.get(stream.run_id, owner)
    assert run.status == "completed"
    answers = [event.payload["text"] for event in events if event.event_type.value == "answer"]
    assert len(answers) == 1
    return run, answers[0]


def test_save_recall_update_restart_forget_and_owner_isolation(tmp_path):
    settings = _settings(tmp_path)
    owner = "github:memory-lifecycle"
    model = FakeToolCallingModel(responses=[AIMessage(content="你好")])
    first = _container(settings, model)
    try:
        _account(first, owner)
        saved_run, notice = _turn(first, owner, "请记住：回答简洁一点")
        original = first.account_memory.list_memories(owner)[0]
        assert original.source_message_id == saved_run.input_message_id
        assert "已保存" in notice
        assert model.calls == []
        _, notice = _turn(first, owner, "请记住：回答简洁一点")
        assert "没有重复新增" in notice
        assert first.account_memory.list_memories(owner) == [original]

        recalled, _ = _turn(first, owner, "你好")
        assert "回答简洁一点" in str(model.calls[-1])
        assert first.account_memory_repo.references(recalled.run_id, owner) == {
            original.memory_id: 1
        }

        updated_run, notice = _turn(first, owner, "请更新这条偏好：回答详细解释")
        updated = first.account_memory.list_memories(owner)[0]
        assert "已更新" in notice
        assert updated.memory_id == original.memory_id
        assert updated.revision == 2
        assert updated.source_message_id == updated_run.input_message_id
        assert updated.source_quote == updated.content == "回答详细解释"
        assert len(model.calls) == 1
    finally:
        first.close()

    model = FakeToolCallingModel(responses=[AIMessage(content="你好")])
    second = _container(settings, model)
    try:
        recalled, _ = _turn(second, owner, "你好")
        assert "回答详细解释" in str(model.calls[-1])
        assert "回答简洁一点" not in str(model.calls[-1])
        assert second.account_memory_repo.references(recalled.run_id, owner) == {
            original.memory_id: 2
        }

        other_owner = "github:another-account"
        _account(second, other_owner)
        _turn(second, other_owner, "你好")
        assert "回答详细解释" not in str(model.calls[-1])
        _, notice = _turn(second, other_owner, "请忘记：回答详细解释")
        assert "没有找到" in notice
        assert len(second.account_memory.list_memories(owner)) == 1

        _, notice = _turn(second, owner, "请忘记我的回答偏好")
        assert "已删除" in notice
        after_delete, _ = _turn(second, owner, "你好")
        assert "回答详细解释" not in str(model.calls[-1])
        assert second.account_memory_repo.references(after_delete.run_id, owner) == {}
    finally:
        second.close()


def test_failed_memory_write_never_acknowledges_success(tmp_path, monkeypatch):
    owner = "github:memory-write-failure"
    model = FakeToolCallingModel(responses=[AIMessage(content="不应调用模型")])
    container = _container(_settings(tmp_path), model)

    def fail_save(*args, **kwargs):
        raise sqlite3.OperationalError("simulated write failure")

    try:
        _account(container, owner)
        monkeypatch.setattr(container.account_memory_repo, "save", fail_save)
        stream = container.run_copilot.stream(
            owner_id=owner, task_id=None, user_message="请记住：回答简洁一点"
        )
        with pytest.raises(sqlite3.OperationalError, match="write failure"):
            list(stream)
        run = container.conversation_run_repo.get(stream.run_id, owner)
        assert run.status == "failed"
        assert run.answer_message_id is None
        assert all(m.role == "user" for m in container.task_repo.list_messages(run.task_id))
        assert container.account_memory.list_memories(owner) == []
        assert model.calls == []
    finally:
        container.close()
