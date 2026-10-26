"""Legacy memory upgrades preserve source data and create a restorable SQLite backup."""

import json
import sqlite3
import time
from pathlib import Path

from infra.storage._db import SqliteConnectionPool
from infra.storage.sqlite_account_memory_repo import SqliteAccountMemoryRepo


def test_legacy_profile_split_lifetimes_backup_and_idempotence(tmp_path):
    pool = SqliteConnectionPool(str(tmp_path / "legacy.sqlite3"))
    SqliteAccountMemoryRepo(pool)
    now = time.time()
    base = {
        "memory_id": "legacy-profile",
        "owner_id": "github:owner",
        "kind": "fact",
        "content": "我是马乐乐，是学生",
        "source_type": "manual",
        "source_task_id": None,
        "source_message_id": None,
        "source_quote": "我是马乐乐，是学生",
        "revision": 2,
        "created_at": now,
        "updated_at": now,
        "expires_at": now + 90 * 86400,
    }
    conn = pool.get()
    conn.execute("CREATE TABLE migration_probe (value TEXT)")
    conn.execute("INSERT INTO migration_probe VALUES ('keep unrelated content')")
    conn.execute(
        "INSERT INTO account_memories VALUES (?, ?, ?, ?)",
        (base["memory_id"], base["owner_id"], base["revision"], json.dumps(base)),
    )
    conn.execute("INSERT INTO account_memory_settings VALUES (?, 1)", (base["owner_id"],))
    conn.commit()
    repo = SqliteAccountMemoryRepo(pool)
    try:
        records = {item.profile_field: item for item in repo.list_memories(base["owner_id"])}
        assert records["name"].memory_id == base["memory_id"]
        assert records["name"].revision == 3
        assert records["name"].value == "马乐乐"
        assert records["name"].expires_at is None
        assert records["role"].value == "学生"
        assert records["role"].expires_at == now + 365 * 86400
        assert records["role"].review_at == now + 180 * 86400
        assert repo.enabled(base["owner_id"]) is True
        assert (
            conn.execute("SELECT value FROM migration_probe").fetchone()[0]
            == "keep unrelated content"
        )
        assert Path(repo.migration_backup).exists()
        backup = sqlite3.connect(repo.migration_backup)
        try:
            original = json.loads(
                backup.execute("SELECT payload FROM account_memories").fetchone()[0]
            )
            assert original == base
            assert (
                backup.execute("SELECT value FROM migration_probe").fetchone()[0]
                == "keep unrelated content"
            )
        finally:
            backup.close()
        again = SqliteAccountMemoryRepo(pool)
        assert again.migration_backup is None
        assert again.list_memories(base["owner_id"]) == repo.list_memories(base["owner_id"])
    finally:
        pool.close()


def test_expired_identity_is_not_revived_and_custom_business_expiry_is_kept(tmp_path):
    pool = SqliteConnectionPool(str(tmp_path / "expired.sqlite3"))
    SqliteAccountMemoryRepo(pool)
    now = time.time()
    for key, content, created, expires in [
        ("expired", "我叫马乐乐，是学生", now - 100 * 86400, now - 10 * 86400),
        ("business", "公司使用新加坡CRM", now, now + 12 * 86400),
    ]:
        item = {
            "memory_id": key,
            "owner_id": "owner",
            "kind": "fact",
            "content": content,
            "source_type": "manual",
            "source_quote": content,
            "revision": 1,
            "created_at": created,
            "updated_at": created,
            "expires_at": expires,
        }
        pool.get().execute(
            "INSERT INTO account_memories VALUES (?, ?, ?, ?)", (key, "owner", 1, json.dumps(item))
        )
    pool.get().commit()
    try:
        repo = SqliteAccountMemoryRepo(pool)
        records = {item.memory_id: item for item in repo.list_memories("owner")}
        assert all(item.kind == "business_context" for item in records.values())
        assert records["expired"].expires_at < now
        assert records["business"].expires_at == now + 12 * 86400
        assert repo.enabled("owner") is False
    finally:
        pool.close()


def test_profile_migration_preserves_extra_business_information(tmp_path):
    pool = SqliteConnectionPool(str(tmp_path / "mixed.sqlite3"))
    SqliteAccountMemoryRepo(pool)
    now = time.time()
    content = "我叫马乐乐，是学生，公司使用新加坡CRM"
    record = {
        "memory_id": "mixed",
        "owner_id": "owner",
        "kind": "fact",
        "content": content,
        "source_type": "manual",
        "source_quote": content,
        "revision": 1,
        "created_at": now,
        "updated_at": now,
        "expires_at": now + 90 * 86400,
    }
    pool.get().execute(
        "INSERT INTO account_memories VALUES (?, ?, ?, ?)",
        ("mixed", "owner", 1, json.dumps(record)),
    )
    pool.get().commit()
    try:
        repo = SqliteAccountMemoryRepo(pool)
        records = repo.list_memories("owner")
        assert {item.profile_field for item in records if item.kind == "profile"} == {
            "name",
            "role",
        }
        business = next(item for item in records if item.kind == "business_context")
        assert business.content == content
        assert business.source_quote == content
        assert business.expires_at == record["expires_at"]
        again = SqliteAccountMemoryRepo(pool)
        assert again.migration_backup is None
        assert again.list_memories("owner") == records
    finally:
        pool.close()


def test_existing_v2_background_can_be_refined_to_explicit_profile(tmp_path):
    pool = SqliteConnectionPool(str(tmp_path / "refine.sqlite3"))
    initial = SqliteAccountMemoryRepo(pool)
    now = time.time()
    from domain.account_memory import AccountMemory

    item = AccountMemory(
        memory_id="refine",
        owner_id="owner",
        kind="business_context",
        content="我是马乐乐，是名学生",
        source_type="manual",
        source_quote="我是马乐乐，是名学生",
        created_at=now,
        updated_at=now,
        expires_at=now + 90 * 86400,
        retention_days=90,
    )
    initial.save(item)
    initial_payload = item.model_dump(exclude={"profile_parser_version"})
    pool.get().execute(
        "UPDATE account_memories SET payload=? WHERE memory_id=?",
        (json.dumps(initial_payload), item.memory_id),
    )
    pool.get().commit()
    try:
        upgraded = SqliteAccountMemoryRepo(pool)
        records = {record.profile_field: record for record in upgraded.list_memories("owner")}
        assert records["name"].value == "马乐乐" and records["name"].expires_at is None
        assert records["role"].value == "学生" and records["role"].retention_days == 365
        assert upgraded.migration_backup is not None
        assert SqliteAccountMemoryRepo(pool).migration_backup is None
    finally:
        pool.close()


def test_explicit_business_type_is_not_reclassified_on_restart(tmp_path):
    from domain.account_memory import AccountMemory

    pool = SqliteConnectionPool(str(tmp_path / "explicit-business.sqlite3"))
    repo = SqliteAccountMemoryRepo(pool)
    now = time.time()
    record = AccountMemory(
        memory_id="business-choice",
        owner_id="owner",
        kind="business_context",
        content="我叫马乐乐，是学生",
        source_type="manual",
        source_quote="我叫马乐乐，是学生",
        created_at=now,
        updated_at=now,
        expires_at=now + 90 * 86400,
    )
    repo.save(record)
    try:
        restarted = SqliteAccountMemoryRepo(pool)
        assert restarted.migration_backup is None
        assert restarted.list_memories("owner") == [record]
    finally:
        pool.close()
