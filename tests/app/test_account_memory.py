import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.use_cases.account_memory import AccountMemoryUseCase
from domain.models import Message, Task, User
from infra.storage._db import SqliteConnectionPool
from infra.storage.sqlite_account_memory_repo import SqliteAccountMemoryRepo
from tests.fakes import InMemoryTaskRepo, InMemoryUserRepo


@pytest.fixture
def memory(tmp_path):
    users = InMemoryUserRepo()
    for name in ["alice", "bob"]:
        users.upsert(
            User(
                user_id=f"github:{name}",
                provider="github",
                provider_id=name,
                display_name=name,
                created_at=1,
                last_active_at=1,
            )
        )
    tasks = InMemoryTaskRepo()
    tasks.create(Task(task_id="task-a", owner_id="github:alice", created_at=1, updated_at=1))
    tasks.append_message(
        Message(
            msg_id="user-a", task_id="task-a", role="user", content="请记住：回答优先列出法规出处"
        )
    )
    tasks.append_message(
        Message(msg_id="assistant-a", task_id="task-a", role="assistant", content="伪造用户偏好")
    )
    pool = SqliteConnectionPool(str(tmp_path / "memory.sqlite3"))
    repo = SqliteAccountMemoryRepo(pool)
    service = AccountMemoryUseCase(repo, users, tasks)
    service.set_enabled("github:alice", True)
    yield service, repo
    pool.close()


def test_memory_is_account_scoped_and_recalled_across_tasks(memory):
    service, _ = memory
    notice = service.handle_message(
        "github:alice", "task-a", "user-a", "请记住：回答优先列出法规出处"
    )
    assert "已保存" in notice
    saved = service.list_memories("github:alice")[0]
    assert saved.source_quote == "回答优先列出法规出处"
    assert service.recall("github:alice", "新对话") == [saved]
    assert service.recall("github:bob", "新对话") == []
    assert service.recall("anon:guest", "新对话") == []


def test_source_forgery_and_sensitive_memory_are_rejected(memory):
    service, _ = memory
    with pytest.raises(ValueError, match="逐字"):
        service.create(
            "github:alice",
            content="不存在的原话",
            kind="fact",
            source_task_id="task-a",
            source_message_id="user-a",
        )
    with pytest.raises(ValueError):
        service.create(
            "github:alice",
            content="伪造用户偏好",
            kind="fact",
            source_task_id="task-a",
            source_message_id="assistant-a",
        )
    with pytest.raises(ValueError):
        service.create("github:alice", content="我的密码是123456", kind="fact")
    with pytest.raises(PermissionError):
        service.create("anon:guest", content="偏好", kind="preference")


def test_edit_delete_disable_and_expiry_invalidate_checkpoints(memory):
    service, repo = memory
    saved = service.create("github:alice", content="回答简洁", kind="preference")
    assert "回答简洁" in service.context("github:alice", "问题", "run-a")
    service.validate_resume("github:alice", "run-a")
    updated = service.edit("github:alice", saved.memory_id, content="回答详细", expected_revision=1)
    assert updated.revision == 2
    with pytest.raises(ValueError, match="修改"):
        service.validate_resume("github:alice", "run-a")
    service.context("github:alice", "问题", "run-b")
    service.set_enabled("github:alice", False)
    assert service.recall("github:alice", "问题") == []
    with pytest.raises(ValueError):
        service.validate_resume("github:alice", "run-b")
    service.set_enabled("github:alice", True)
    service.delete("github:alice", saved.memory_id)
    assert repo.get("github:alice", saved.memory_id) is None
    with pytest.raises(ValueError):
        service.validate_resume("github:alice", "run-b")
    expired = service.create(
        "github:alice", content="公司背景", kind="fact", expires_at=time.time() + 10
    )
    service._clock = lambda: time.time() + 20
    assert service.list_memories("github:alice") == []
    assert repo.get("github:alice", expired.memory_id) is None


def test_edit_keeps_expiry_and_facts_cannot_become_permanent(memory):
    service, _ = memory
    original = service.create("github:alice", content="公司在上海", kind="fact")
    edited = service.edit(
        "github:alice", original.memory_id, content="公司在北京", expected_revision=1
    )
    assert edited.expires_at == original.expires_at
    with pytest.raises(ValueError, match="到期"):
        service.edit(
            "github:alice",
            original.memory_id,
            content="公司在深圳",
            expected_revision=2,
            expires_at=None,
        )
    changed = service.edit(
        "github:alice",
        original.memory_id,
        content="公司在深圳",
        expected_revision=2,
        expires_at=time.time() + 10,
    )
    service._clock = lambda: time.time() + 20
    with pytest.raises(ValueError, match="过期"):
        service.edit("github:alice", changed.memory_id, content="公司背景", expected_revision=3)


def test_empty_snapshot_never_recalls_later_memories(memory):
    service, repo = memory
    assert service.context("github:alice", "问题", "run-empty") == ""
    service.create("github:alice", content="回答简洁", kind="preference")
    assert service.context("github:alice", "问题", "run-empty") == ""
    assert service.for_run("github:alice", "run-empty") == []
    assert repo.references("run-empty", "github:alice") == {}


def test_stale_cleanup_cannot_delete_new_revision(memory):
    service, repo = memory
    original = service.create("github:alice", content="回答简洁", kind="preference")
    edited = service.edit(
        "github:alice", original.memory_id, content="回答详细", expected_revision=1
    )
    assert repo.delete("github:alice", original.memory_id, expected_revision=1) is False
    assert repo.get("github:alice", original.memory_id).revision == edited.revision


def _command(service, text, *, owner_id="github:alice", task_id="task-a"):
    message_id = f"cmd-{len(service._tasks.list_messages(task_id))}"
    service._tasks.append_message(
        Message(msg_id=message_id, task_id=task_id, role="user", content=text)
    )
    return service.handle_message(owner_id, task_id, message_id, text), message_id


def test_repeated_save_preserves_source_expiry_revision_and_run_references(memory):
    service, repo = memory
    _, source_id = _command(service, "请记住：公司使用新加坡CRM")
    original = service.list_memories("github:alice")[0]
    service.context("github:alice", "新加坡CRM", "run-original")

    notice, _ = _command(service, "请记住：公司使用新加坡CRM")

    assert "没有重复新增" in notice
    assert service.list_memories("github:alice") == [original]
    assert original.source_message_id == source_id
    assert repo.references("run-original", "github:alice") == {original.memory_id: 1}
    service.validate_resume("github:alice", "run-original")


def test_explicit_update_has_new_user_source_and_forget_invalidates_old_run(memory):
    service, repo = memory
    _command(service, "请记住：回答简洁一点")
    original = service.list_memories("github:alice")[0]
    service.context("github:alice", "你好", "run-old")

    notice, update_source = _command(service, "请更新记忆：回答简洁一点 => 回答详细解释")

    assert "已更新" in notice
    updated = service.list_memories("github:alice")[0]
    assert updated.memory_id == original.memory_id
    assert updated.revision == 2
    assert updated.source_type == "user_message"
    assert updated.source_message_id == update_source
    assert updated.source_quote == updated.content == "回答详细解释"
    with pytest.raises(ValueError, match="修改"):
        service.validate_resume("github:alice", "run-old")

    service.context("github:alice", "你好", "run-new")
    notice, _ = _command(service, "请忘记：回答详细解释")
    assert "已删除" in notice
    assert service.recall("github:alice", "你好") == []
    assert repo.get("github:alice", updated.memory_id) is None
    with pytest.raises(ValueError, match="删除"):
        service.validate_resume("github:alice", "run-new")


def test_ambiguous_update_and_forget_leave_every_record_unchanged(memory):
    service, _ = memory
    _command(service, "请记住：回答简洁一点")
    _command(service, "请记住：回答优先列出法规出处")
    original = service.list_memories("github:alice")
    for command in ["请更新这条偏好：回答详细解释", "请忘记我的回答偏好"]:
        with pytest.raises(ValueError, match="多条"):
            _command(service, command)
        assert service.list_memories("github:alice") == original
    notice, _ = _command(service, "请把记忆“回答简洁一点”改为“回答详细解释”")
    assert "已更新" in notice


def test_command_must_match_persisted_user_message_and_owner(memory):
    service, _ = memory
    _command(service, "请记住：回答简洁一点")
    original = service.list_memories("github:alice")
    with pytest.raises(ValueError, match="指令必须"):
        service.handle_message("github:alice", "task-a", "user-a", "请清空我的记忆")
    with pytest.raises(ValueError, match="会话不可用"):
        service.handle_message("github:bob", "task-a", "user-a", "请清空我的记忆")
    with pytest.raises(ValueError, match="逐字"):
        service.handle_message("github:alice", "task-a", "assistant-a", "请清空我的记忆")
    assert service.list_memories("github:alice") == original


def test_update_cannot_create_duplicate_and_fact_keeps_expiry(memory):
    service, _ = memory
    _command(service, "请记住：公司使用新加坡CRM")
    original = service.list_memories("github:alice")[0]
    _command(service, "请更新这条背景：公司使用德国CRM")
    updated = service.list_memories("github:alice")[0]
    assert updated.kind == "business_context"
    assert updated.expires_at == original.expires_at
    _command(service, "请记住：公司使用新加坡CRM")
    with pytest.raises(ValueError, match="相同内容"):
        _command(service, "请更新记忆：公司使用德国CRM => 公司使用新加坡CRM")
    assert len(service.list_memories("github:alice")) == 2


def test_concurrent_identical_saves_create_one_record(memory):
    service, repo = memory

    def save(_):
        try:
            return service.create("github:alice", content="回答简洁", kind="preference")
        finally:
            repo._pool.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        saved = list(pool.map(save, range(8)))
    assert len({item.memory_id for item in saved}) == 1
    assert service.list_memories("github:alice") == [saved[0]]


def test_quota_check_allows_duplicates_and_is_atomic(memory):
    service, repo = memory
    for i in range(199):
        service.create("github:alice", content=f"背景{i}", kind="fact")

    def save(content):
        try:
            return service.create("github:alice", content=content, kind="fact")
        except ValueError as error:
            return str(error)
        finally:
            repo._pool.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ["新背景A", "新背景B"]))
    assert sum(isinstance(item, str) for item in results) == 1
    assert len(service.list_memories("github:alice")) == 200
    assert service.create("github:alice", content="背景0", kind="fact").content == "背景0"


def test_list_and_clear_commands_do_not_clear_another_account(memory):
    service, _ = memory
    _command(service, "请记住：回答简洁一点")
    other = service.create("github:bob", content="回答详细解释", kind="preference")
    notice, _ = _command(service, "查看我的记忆")
    assert "回答简洁一点" in notice
    assert "回答详细解释" not in notice
    notice, _ = _command(service, "请清空我的记忆")
    assert "已清空 1 条" in notice
    assert service.list_memories("github:alice") == []
    assert service.list_memories("github:bob") == [other]


def test_corrected_older_preference_is_prioritized_for_next_recall(memory):
    service, _ = memory
    for i in range(5):
        service.create("github:alice", content=f"回答偏好{i}", kind="preference")
    assert all(item.content != "回答偏好0" for item in service.recall("github:alice", "你好"))
    _command(service, "请更新记忆：回答偏好0 => 回答优先给出法规依据")
    assert service.recall("github:alice", "你好")[0].content == "回答优先给出法规依据"
