"""TaskManagementUseCase 单测，依赖 InMemoryTaskRepo。"""

from __future__ import annotations

import pytest

from app.use_cases.task_management import TaskManagementUseCase
from tests.fakes import InMemoryTaskRepo


def _uc() -> TaskManagementUseCase:
    return TaskManagementUseCase(InMemoryTaskRepo())


class TestCreate:
    def test_create_returns_task_with_owner(self) -> None:
        uc = _uc()
        t = uc.create_task("anon:abc", title="出境咨询", user_goal="想做评估")
        assert t.owner_id == "anon:abc"
        assert t.task_id.startswith("task_")
        assert t.state == "planning"
        assert t.title == "出境咨询"
        assert t.user_goal == "想做评估"

    def test_create_rejects_empty_owner(self) -> None:
        with pytest.raises(ValueError):
            _uc().create_task("")


class TestQueryAndDelete:
    def test_list_filters_by_owner(self) -> None:
        uc = _uc()
        uc.create_task("anon:a", title="a1")
        uc.create_task("anon:a", title="a2")
        uc.create_task("anon:b", title="b1")
        assert len(uc.list_tasks("anon:a")) == 2
        assert len(uc.list_tasks("anon:b")) == 1
        assert uc.list_tasks("nobody") == []

    def test_get_returns_none_for_wrong_owner(self) -> None:
        uc = _uc()
        t = uc.create_task("anon:a", title="a1")
        assert uc.get_task(t.task_id, "anon:a") is not None
        assert uc.get_task(t.task_id, "anon:b") is None

    def test_delete_respects_owner(self) -> None:
        uc = _uc()
        t = uc.create_task("anon:a")
        assert uc.delete_task(t.task_id, "anon:b") is False
        assert uc.delete_task(t.task_id, "anon:a") is True
        assert uc.get_task(t.task_id, "anon:a") is None


class TestFactsMerge:
    def test_update_facts_shallow_merge(self) -> None:
        uc = _uc()
        t = uc.create_task("anon:a")
        uc.update_facts(t.task_id, "anon:a", {"region": "EU"})
        updated = uc.update_facts(t.task_id, "anon:a", {"category": "PI"})
        assert updated is not None
        assert updated.collected_facts == {"region": "EU", "category": "PI"}
        assert updated.updated_at >= t.updated_at

    def test_update_facts_wrong_owner_returns_none(self) -> None:
        uc = _uc()
        t = uc.create_task("anon:a")
        assert uc.update_facts(t.task_id, "anon:b", {"x": 1}) is None


class TestMessages:
    def test_append_user_and_assistant_messages(self) -> None:
        uc = _uc()
        t = uc.create_task("anon:a")
        uc.append_user_message(t.task_id, "anon:a", "你好")
        uc.append_assistant_message(t.task_id, "anon:a", "您好，需要什么帮助？")
        msgs = uc.list_messages(t.task_id, "anon:a")
        assert [m.role for m in msgs] == ["user", "assistant"]
        assert [m.content for m in msgs] == ["你好", "您好，需要什么帮助？"]

    def test_append_message_rejects_wrong_owner(self) -> None:
        uc = _uc()
        t = uc.create_task("anon:a")
        with pytest.raises(PermissionError):
            uc.append_user_message(t.task_id, "anon:b", "hi")

    def test_list_messages_rejects_wrong_owner(self) -> None:
        uc = _uc()
        t = uc.create_task("anon:a")
        with pytest.raises(PermissionError):
            uc.list_messages(t.task_id, "anon:b")
