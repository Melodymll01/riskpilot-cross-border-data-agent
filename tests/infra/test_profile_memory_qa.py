"""Persisted profile questions use memory sources without relaxing legal verification."""

import pytest
from langchain_core.messages import AIMessage

from domain.models import Message, User
from tests.fakes import FakeToolCallingModel
from tests.infra.test_account_memory_lifecycle import _turn
from tests.infra.test_conversation_checkpoint_recovery import _container, _settings


def account(container, owner):
    container.user_repo.upsert(
        User(
            user_id=owner,
            provider="github",
            provider_id=owner,
            display_name="Not a profile source",
            created_at=1,
            last_active_at=1,
        )
    )


def test_profile_save_answer_correct_forget_restart_and_source_citations(tmp_path):
    settings = _settings(tmp_path)
    owner = "github:profile"
    model = FakeToolCallingModel(responses=[AIMessage(content="不应调用")])
    first = _container(settings, model)
    try:
        account(first, owner)
        _, notice = _turn(first, owner, "请记住：我是马乐乐，是学生")
        assert "2 条" in notice
        _, disabled = _turn(first, owner, "我的职业是什么")
        assert "已关闭" in disabled and "学生" not in disabled
        _turn(first, owner, "开启记忆")
        run, answer = _turn(first, owner, "我的职业是什么")
        assert "学生" in answer
        message = next(
            item for item in first.task_repo.list_messages(run.task_id) if item.role == "assistant"
        )
        assert message.citations[0].source_type == "memory"
        assert message.citations[0].text_snippet == "是学生"
        _, answer = _turn(first, owner, "我叫什么名字")
        assert "马乐乐" in answer
        _turn(first, owner, "请更新我的职业：工程师")
        _, answer = _turn(first, owner, "我的职业是什么")
        assert "工程师" in answer and "学生" not in answer
        assert model.calls == []
    finally:
        first.close()
    second = _container(settings, FakeToolCallingModel(responses=[AIMessage(content="不应调用")]))
    try:
        _, answer = _turn(second, owner, "我叫什么")
        assert "马乐乐" in answer
        _turn(second, owner, "请忘记我的姓名")
        _, answer = _turn(second, owner, "我叫什么")
        assert "还没有保存" in answer
        assert "马乐乐" not in answer
    finally:
        second.close()


def test_current_correction_wins_without_rewriting_account_profile(tmp_path):
    container = _container(
        _settings(tmp_path, memory_enabled=True, memory_summary_enabled=False),
        FakeToolCallingModel(responses=[AIMessage(content="不应调用")]),
    )
    owner = "github:current-profile"
    try:
        account(container, owner)
        container.account_memory.set_enabled(owner, True)
        role = container.account_memory.create(
            owner, kind="profile", profile_field="role", content="学生"
        )
        task = container.task_management.create_task(owner, title="current")
        container.task_repo.append_message(
            Message(msg_id="new-role", task_id=task.task_id, role="user", content="我现在是工程师")
        )
        stream = container.run_copilot.stream(
            owner_id=owner, task_id=task.task_id, user_message="我的职业是什么"
        )
        events = list(stream)
        answer = next(
            event.payload["text"] for event in events if event.event_type.value == "answer"
        )
        assert "工程师" in answer and "学生" not in answer
        assert container.account_memory_repo.get(owner, role.memory_id).value == "学生"
        assert container.account_memory_repo.references(stream.run_id, owner) == {}
    finally:
        container.close()


def test_deleted_profile_between_read_and_commit_cannot_be_published(tmp_path):
    container = _container(
        _settings(tmp_path), FakeToolCallingModel(responses=[AIMessage(content="不应调用")])
    )
    owner = "github:profile-race"
    try:
        account(container, owner)
        container.account_memory.set_enabled(owner, True)
        memory = container.account_memory.create(
            owner, kind="profile", profile_field="name", content="马乐乐"
        )
        commit = container.conversation_run_repo.commit_terminal

        def delete_before_commit(run, message, *, validate_context=None):
            container.account_memory.delete(owner, memory.memory_id)
            return commit(run, message, validate_context=validate_context)

        container.conversation_run_repo.commit_terminal = delete_before_commit
        stream = container.run_copilot.stream(owner_id=owner, task_id=None, user_message="我叫什么")
        with pytest.raises(ValueError, match="记忆已"):
            list(stream)
        run = container.conversation_run_repo.get(stream.run_id, owner)
        assert run.answer_message_id is None
        assert all(item.role == "user" for item in container.task_repo.list_messages(run.task_id))
    finally:
        container.close()
