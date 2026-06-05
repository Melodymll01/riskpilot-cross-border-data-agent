"""SqliteUserRepo / SqliteTaskRepo 集成测试（使用临时文件 DB）。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from domain.models import (
    Artifact,
    Citation,
    Message,
    Task,
    ToolCall,
    User,
)
from domain.ports import TaskRepoPort, UserRepoPort
from infra.storage import SqliteTaskRepo, SqliteUserRepo
from infra.storage._db import SqliteConnectionPool


@pytest.fixture
def pool(tmp_path: Path) -> SqliteConnectionPool:
    return SqliteConnectionPool(str(tmp_path / "app.db"))


@pytest.fixture
def user_repo(pool: SqliteConnectionPool) -> SqliteUserRepo:
    return SqliteUserRepo(pool)


@pytest.fixture
def task_repo(pool: SqliteConnectionPool) -> SqliteTaskRepo:
    return SqliteTaskRepo(pool)


def _now() -> float:
    return time.time()


def _user(user_id: str = "github:alice", display_name: str = "Alice") -> User:
    t = _now()
    return User(
        user_id=user_id,
        provider="github",
        provider_id="alice",
        email=None,
        display_name=display_name,
        avatar_url=None,
        created_at=t,
        last_active_at=t,
    )


def _task(task_id: str, owner_id: str) -> Task:
    t = _now()
    return Task(
        task_id=task_id,
        owner_id=owner_id,
        title="测试任务",
        state="planning",
        user_goal="数据出境合规咨询",
        collected_facts={"region": "EU"},
        created_at=t,
        updated_at=t,
    )


# ── 契约（isinstance）测试 ─────────────────────────────────────────────


class TestProtocolConformance:
    def test_user_repo_is_user_repo_port(self, user_repo: SqliteUserRepo) -> None:
        assert isinstance(user_repo, UserRepoPort)

    def test_task_repo_is_task_repo_port(self, task_repo: SqliteTaskRepo) -> None:
        assert isinstance(task_repo, TaskRepoPort)


# ── UserRepo ──────────────────────────────────────────────────────────


class TestUserRepo:
    def test_upsert_then_get(self, user_repo: SqliteUserRepo) -> None:
        u = _user()
        user_repo.upsert(u)
        loaded = user_repo.get("github:alice")
        assert loaded is not None
        assert loaded.user_id == "github:alice"
        assert loaded.display_name == "Alice"
        assert loaded.provider == "github"

    def test_upsert_updates_existing(self, user_repo: SqliteUserRepo) -> None:
        user_repo.upsert(_user(display_name="Old"))
        user_repo.upsert(_user(display_name="New"))
        loaded = user_repo.get("github:alice")
        assert loaded is not None
        assert loaded.display_name == "New"

    def test_get_missing_returns_none(self, user_repo: SqliteUserRepo) -> None:
        assert user_repo.get("ghost") is None

    def test_touch_updates_last_active(self, user_repo: SqliteUserRepo) -> None:
        u = _user()
        user_repo.upsert(u)
        time.sleep(0.01)
        user_repo.touch(u.user_id)
        loaded = user_repo.get(u.user_id)
        assert loaded is not None
        assert loaded.last_active_at >= u.last_active_at

    def test_merge_owner_moves_tasks(
        self,
        user_repo: SqliteUserRepo,
        task_repo: SqliteTaskRepo,
    ) -> None:
        anon = _user(user_id="anon:x", display_name="anon")
        gh = _user(user_id="github:bob", display_name="Bob")
        user_repo.upsert(anon)
        user_repo.upsert(gh)

        task_repo.create(_task("t1", "anon:x"))
        task_repo.create(_task("t2", "anon:x"))
        task_repo.create(_task("t3", "github:bob"))

        moved = user_repo.merge_owner("anon:x", "github:bob")
        assert moved == 2

        bob_tasks = task_repo.list_for_owner("github:bob")
        assert {t.task_id for t in bob_tasks} == {"t1", "t2", "t3"}
        assert task_repo.list_for_owner("anon:x") == []

    def test_merge_owner_same_id_noop(self, user_repo: SqliteUserRepo) -> None:
        assert user_repo.merge_owner("a", "a") == 0


# ── TaskRepo ──────────────────────────────────────────────────────────


class TestTaskRepo:
    def test_create_then_get_by_owner(self, task_repo: SqliteTaskRepo) -> None:
        task_repo.create(_task("t1", "github:alice"))
        loaded = task_repo.get("t1", "github:alice")
        assert loaded is not None
        assert loaded.title == "测试任务"
        assert loaded.collected_facts == {"region": "EU"}

    def test_get_wrong_owner_returns_none(self, task_repo: SqliteTaskRepo) -> None:
        task_repo.create(_task("t1", "github:alice"))
        assert task_repo.get("t1", "github:bob") is None

    def test_list_for_owner_orders_by_updated_at_desc(
        self, task_repo: SqliteTaskRepo
    ) -> None:
        for i, ts in enumerate([100.0, 200.0, 50.0]):
            t = _task(f"t{i}", "github:alice").model_copy(
                update={"updated_at": ts, "created_at": ts}
            )
            task_repo.create(t)
        result = task_repo.list_for_owner("github:alice")
        assert [t.task_id for t in result] == ["t1", "t0", "t2"]

    def test_update(self, task_repo: SqliteTaskRepo) -> None:
        t = _task("t1", "github:alice")
        task_repo.create(t)
        updated = t.model_copy(update={"state": "answering", "title": "已更新"})
        task_repo.update(updated)
        loaded = task_repo.get("t1", "github:alice")
        assert loaded is not None
        assert loaded.state == "answering"
        assert loaded.title == "已更新"

    def test_delete(self, task_repo: SqliteTaskRepo) -> None:
        task_repo.create(_task("t1", "github:alice"))
        assert task_repo.delete("t1", "github:alice") is True
        assert task_repo.get("t1", "github:alice") is None
        assert task_repo.delete("t1", "github:alice") is False

    def test_delete_wrong_owner_fails(self, task_repo: SqliteTaskRepo) -> None:
        task_repo.create(_task("t1", "github:alice"))
        assert task_repo.delete("t1", "github:bob") is False
        assert task_repo.get("t1", "github:alice") is not None

    def test_append_and_list_messages(self, task_repo: SqliteTaskRepo) -> None:
        task_repo.create(_task("t1", "github:alice"))
        m1 = Message(
            msg_id="m1", task_id="t1", role="user", content="你好",
            citations=[Citation(source_type="law", source_name="PIPL", text_snippet="x")],
        )
        m2 = Message(msg_id="m2", task_id="t1", role="assistant", content="您好")
        task_repo.append_message(m1)
        time.sleep(0.001)
        task_repo.append_message(m2)

        msgs = task_repo.list_messages("t1")
        assert [m.msg_id for m in msgs] == ["m1", "m2"]
        assert msgs[0].citations[0].source_name == "PIPL"

    def test_append_message_updates_task_updated_at(
        self, task_repo: SqliteTaskRepo
    ) -> None:
        t = _task("t1", "github:alice").model_copy(update={"updated_at": 100.0})
        task_repo.create(t)
        m = Message(
            msg_id="m1", task_id="t1", role="user", content="x", created_at=999.0
        )
        task_repo.append_message(m)
        loaded = task_repo.get("t1", "github:alice")
        assert loaded is not None
        assert loaded.updated_at == 999.0

    def test_append_tool_call_upsert(self, task_repo: SqliteTaskRepo) -> None:
        task_repo.create(_task("t1", "github:alice"))
        c1 = ToolCall(
            tool_call_id="c1", task_id="t1", tool_name="retrieve",
            input_json={"q": "x"}, status="pending",
        )
        task_repo.append_tool_call(c1)
        c1_done = c1.model_copy(
            update={"status": "success", "output_json": {"hits": 3}, "duration_ms": 12}
        )
        task_repo.append_tool_call(c1_done)
        # 通过直接查 DB 验证 upsert 成功
        conn = task_repo._pool.get()
        row = conn.execute(
            "SELECT status, duration_ms FROM tool_calls WHERE tool_call_id=?",
            ("c1",),
        ).fetchone()
        assert row["status"] == "success"
        assert row["duration_ms"] == 12

    def test_append_artifact(self, task_repo: SqliteTaskRepo) -> None:
        task_repo.create(_task("t1", "github:alice"))
        a = Artifact(
            artifact_id="a1", task_id="t1", artifact_type="risk_profile",
            payload_json={"score": 0.8},
        )
        task_repo.append_artifact(a)
        conn = task_repo._pool.get()
        row = conn.execute(
            "SELECT artifact_type, payload_json FROM artifacts WHERE artifact_id=?",
            ("a1",),
        ).fetchone()
        assert row["artifact_type"] == "risk_profile"

    def test_cascade_delete_messages(self, task_repo: SqliteTaskRepo) -> None:
        task_repo.create(_task("t1", "github:alice"))
        task_repo.append_message(
            Message(msg_id="m1", task_id="t1", role="user", content="x")
        )
        task_repo.delete("t1", "github:alice")
        assert task_repo.list_messages("t1") == []


# ── Step 012-tail: Task.mode 字段 ──────────────────────────────────────


class TestTaskMode:
    def test_default_mode_is_qa(self, task_repo: SqliteTaskRepo) -> None:
        task_repo.create(_task("t1", "github:alice"))  # _task 不传 mode
        loaded = task_repo.get("t1", "github:alice")
        assert loaded is not None
        assert loaded.mode == "qa"

    def test_create_with_research_mode_persists(
        self, task_repo: SqliteTaskRepo
    ) -> None:
        t = _task("t1", "github:alice").model_copy(update={"mode": "research"})
        task_repo.create(t)
        loaded = task_repo.get("t1", "github:alice")
        assert loaded is not None
        assert loaded.mode == "research"

    def test_create_with_profile_mode_persists(
        self, task_repo: SqliteTaskRepo
    ) -> None:
        t = _task("t1", "github:alice").model_copy(update={"mode": "profile"})
        task_repo.create(t)
        loaded = task_repo.get("t1", "github:alice")
        assert loaded is not None
        assert loaded.mode == "profile"

    def test_update_changes_mode(self, task_repo: SqliteTaskRepo) -> None:
        task_repo.create(_task("t1", "github:alice"))
        loaded = task_repo.get("t1", "github:alice")
        assert loaded is not None
        updated = loaded.model_copy(
            update={"mode": "research", "updated_at": _now()}
        )
        task_repo.update(updated)
        again = task_repo.get("t1", "github:alice")
        assert again is not None and again.mode == "research"


class TestTaskModeMigration:
    """验证老库（无 mode 列）打开时自动 ALTER。"""

    def test_legacy_db_without_mode_column_gets_migrated(
        self, tmp_path: Path
    ) -> None:
        import sqlite3

        db_path = tmp_path / "legacy.db"
        # 1) 手工建一个不含 mode 列的 tasks 表（模拟老库）
        conn = sqlite3.connect(str(db_path))
        conn.executescript(
            """
            CREATE TABLE tasks (
                task_id          TEXT PRIMARY KEY,
                owner_id         TEXT NOT NULL,
                title            TEXT NOT NULL DEFAULT '',
                state            TEXT NOT NULL DEFAULT 'planning',
                user_goal        TEXT NOT NULL DEFAULT '',
                collected_facts  TEXT NOT NULL DEFAULT '{}',
                created_at       REAL NOT NULL,
                updated_at       REAL NOT NULL
            );
            """
        )
        now = _now()
        conn.execute(
            "INSERT INTO tasks (task_id, owner_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            ("legacy-t1", "github:alice", now, now),
        )
        conn.commit()
        conn.close()

        # 2) 通过 SqliteConnectionPool 打开：应自动 ALTER 加 mode 列，老数据默认 'qa'
        pool = SqliteConnectionPool(str(db_path))
        repo = SqliteTaskRepo(pool)
        loaded = repo.get("legacy-t1", "github:alice")
        assert loaded is not None
        assert loaded.mode == "qa"
