"""``TaskBackedMemory`` L1 短期记忆测试（S-030a）。

覆盖三类重点之"隔离"：owner_id 校验必须生效，跨用户绝不泄露。
"""

from __future__ import annotations

import time

import pytest

from domain.models import Message, Task
from infra.memory import TaskBackedMemory
from tests.fakes.fake_repos import InMemoryTaskRepo

pytestmark = pytest.mark.unit


def _seed_task(repo: InMemoryTaskRepo, *, task_id: str, owner_id: str) -> None:
    now = time.time()
    repo.create(
        Task(
            task_id=task_id,
            owner_id=owner_id,
            title="t",
            state="planning",
            user_goal="",
            collected_facts={},
            created_at=now,
            updated_at=now,
        )
    )


def _msg(task_id: str, role: str, content: str, ts: float) -> Message:
    return Message(
        msg_id=f"m_{ts}",
        task_id=task_id,
        role=role,  # type: ignore[arg-type]
        content=content,
        created_at=ts,
    )


class TestRecentMessages:
    def test_returns_last_n_in_order(self) -> None:
        repo = InMemoryTaskRepo()
        _seed_task(repo, task_id="t1", owner_id="anon:o1")
        for i in range(5):
            repo.append_message(_msg("t1", "user", f"q{i}", 1000.0 + i))
        mem = TaskBackedMemory(repo)

        out = mem.recent_messages("anon:o1", "t1", 3)

        assert [m.content for m in out] == ["q2", "q3", "q4"]

    def test_fewer_than_n_returns_all(self) -> None:
        repo = InMemoryTaskRepo()
        _seed_task(repo, task_id="t1", owner_id="anon:o1")
        repo.append_message(_msg("t1", "user", "only", 1.0))
        mem = TaskBackedMemory(repo)

        out = mem.recent_messages("anon:o1", "t1", 10)

        assert [m.content for m in out] == ["only"]

    def test_empty_task_returns_empty(self) -> None:
        repo = InMemoryTaskRepo()
        _seed_task(repo, task_id="t1", owner_id="anon:o1")
        mem = TaskBackedMemory(repo)

        assert mem.recent_messages("anon:o1", "t1", 5) == []

    def test_n_zero_returns_empty(self) -> None:
        repo = InMemoryTaskRepo()
        _seed_task(repo, task_id="t1", owner_id="anon:o1")
        repo.append_message(_msg("t1", "user", "q", 1.0))
        mem = TaskBackedMemory(repo)

        assert mem.recent_messages("anon:o1", "t1", 0) == []


class TestOwnerIsolation:
    def test_other_owner_gets_empty_no_leak(self) -> None:
        repo = InMemoryTaskRepo()
        _seed_task(repo, task_id="t1", owner_id="anon:owner_a")
        repo.append_message(_msg("t1", "user", "secret", 1.0))
        mem = TaskBackedMemory(repo)

        # owner_b 读 owner_a 的 task：必须空，不得泄露 "secret"
        out = mem.recent_messages("anon:owner_b", "t1", 5)

        assert out == []

    def test_owner_sees_own_messages(self) -> None:
        repo = InMemoryTaskRepo()
        _seed_task(repo, task_id="t1", owner_id="anon:owner_a")
        repo.append_message(_msg("t1", "user", "mine", 1.0))
        mem = TaskBackedMemory(repo)

        out = mem.recent_messages("anon:owner_a", "t1", 5)

        assert [m.content for m in out] == ["mine"]

    def test_unknown_task_returns_empty(self) -> None:
        repo = InMemoryTaskRepo()
        mem = TaskBackedMemory(repo)

        assert mem.recent_messages("anon:o1", "does_not_exist", 5) == []


class TestL2L3L4NotImplemented:
    def test_summary_raises(self) -> None:
        mem = TaskBackedMemory(InMemoryTaskRepo())
        with pytest.raises(NotImplementedError):
            mem.get_summary("t1")
