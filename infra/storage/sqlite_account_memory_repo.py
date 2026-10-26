"""SQLite account memory and run revision references, always scoped by owner."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from domain.account_memory import AccountMemory, AccountMemoryConflict
from domain.memory_policy import MemoryPolicy, profile_declarations
from infra.storage._db import SqliteConnectionPool


class SqliteAccountMemoryRepo:
    def __init__(self, pool: SqliteConnectionPool, *, policy: MemoryPolicy | None = None) -> None:
        self._pool = pool
        self._policy = policy or MemoryPolicy()
        self.migration_backup: str | None = None
        self._pool.get().executescript("""
            CREATE TABLE IF NOT EXISTS account_memories (
                memory_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
                revision INTEGER NOT NULL, payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_account_memories_owner ON account_memories(owner_id);
            CREATE TABLE IF NOT EXISTS account_memory_settings (
                owner_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS run_account_memories (
                run_id TEXT NOT NULL, owner_id TEXT NOT NULL, memory_id TEXT NOT NULL,
                revision INTEGER NOT NULL, PRIMARY KEY(run_id, owner_id, memory_id)
            );
            CREATE TABLE IF NOT EXISTS run_memory_snapshots (
                run_id TEXT NOT NULL, owner_id TEXT NOT NULL,
                PRIMARY KEY(run_id, owner_id)
            );
            INSERT OR IGNORE INTO run_memory_snapshots SELECT DISTINCT run_id, owner_id
                FROM run_account_memories;
        """)
        self._migrate_legacy()

    def _migrate_legacy(self) -> None:
        conn = self._pool.get()
        candidates = conn.execute(
            "SELECT payload FROM account_memories WHERE COALESCE(json_extract(payload, '$.schema_version'), 1)<2 "
            "OR (json_extract(payload, '$.kind')='business_context' "
            "AND COALESCE(json_extract(payload, '$.profile_parser_version'), 0)<1) "
            "ORDER BY json_extract(payload, '$.updated_at') ASC"
        ).fetchall()
        rows = []
        for row in candidates:
            payload = json.loads(row[0])
            legacy = payload.get("schema_version", 1) < 2
            try:
                refinable = (
                    payload.get("source_quote") == payload["content"]
                    and (payload.get("expires_at") is None or payload["expires_at"] > time.time())
                    and bool(profile_declarations(payload["content"]))
                )
            except ValueError:
                refinable = False
            if legacy or refinable:
                rows.append(row)
        if not rows:
            return
        if self._pool.path != ":memory:":
            original = Path(self._pool.path)
            folder = original.parent / "backups"
            folder.mkdir(exist_ok=True)
            backup = folder / f"{original.stem}-memory-v1-{datetime.now():%Y%m%d-%H%M%S-%f}.sqlite3"
            target = sqlite3.connect(backup)
            try:
                conn.backup(target)
            finally:
                target.close()
            self.migration_backup = str(backup)
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            for row in rows:
                raw = json.loads(row[0])
                old_kind = raw["kind"]
                base = {
                    **raw,
                    "schema_version": 2,
                    "profile_parser_version": 1,
                    "review_at": None,
                    "retention_days": None,
                }
                if raw.get("expires_at"):
                    base["retention_days"] = max(
                        (raw["expires_at"] - raw["created_at"]) / 86400, 0.0001
                    )
                try:
                    declarations = (
                        profile_declarations(raw["content"])
                        if old_kind in {"fact", "business_context"}
                        and raw.get("source_quote") == raw["content"]
                        else []
                    )
                except ValueError:
                    declarations = []
                if raw.get("expires_at") is not None and raw["expires_at"] <= time.time():
                    declarations = []  # Do not revive expired records through a schema upgrade.
                if declarations:
                    conn.execute(
                        "DELETE FROM account_memories WHERE memory_id=? AND owner_id=?",
                        (raw["memory_id"], raw["owner_id"]),
                    )
                    for index, declaration in enumerate(declarations):
                        stamp = raw["updated_at"]
                        default_expiry = raw.get("expires_at") == raw["created_at"] + 90 * 86400
                        custom = None if default_expiry else raw.get("expires_at")
                        expiry, review = self._policy.lifecycle(
                            "profile", declaration.field, stamp, custom
                        )
                        memory_id = (
                            raw["memory_id"]
                            if index == 0
                            else "mem_"
                            + hashlib.sha256(
                                f"{raw['memory_id']}:{declaration.field}".encode()
                            ).hexdigest()[:32]
                        )
                        upgraded = AccountMemory.model_validate(
                            {
                                **base,
                                "memory_id": memory_id,
                                "kind": "profile",
                                "content": declaration.quote,
                                "source_quote": declaration.quote,
                                "profile_field": declaration.field,
                                "value": declaration.value,
                                "expires_at": expiry,
                                "review_at": review,
                                "retention_days": (expiry - stamp) / 86400 if expiry else None,
                                "revision": raw["revision"] + 1 if index == 0 else 1,
                            }
                        )
                        self._save_locked(upgraded, enforce_quota=False)
                    clauses = [
                        part.strip().rstrip("。.!！")
                        for part in re.split(r"[，,；;\n]", raw["content"])
                        if part.strip()
                    ]
                    quotes = {item.quote for item in declarations}
                    if any(part not in quotes for part in clauses):
                        # Preserve the complete original context if it contains additional facts.
                        contextual = AccountMemory.model_validate(
                            {
                                **base,
                                "kind": "business_context",
                                "memory_id": "mem_"
                                + hashlib.sha256(
                                    f"{raw['memory_id']}:context".encode()
                                ).hexdigest()[:32],
                                "revision": 1,
                            }
                        )
                        self._save_locked(contextual, enforce_quota=False)
                else:
                    base["kind"] = "business_context" if old_kind == "fact" else old_kind
                    if old_kind == "fact":
                        base["revision"] = raw["revision"] + 1
                    upgraded = AccountMemory.model_validate(base)
                    conn.execute(
                        "UPDATE account_memories SET revision=?, payload=? WHERE memory_id=? AND owner_id=?",
                        (
                            upgraded.revision,
                            upgraded.model_dump_json(),
                            upgraded.memory_id,
                            upgraded.owner_id,
                        ),
                    )

    def list_memories(self, owner_id: str) -> list[AccountMemory]:
        rows = self._pool.get().execute(
            "SELECT payload FROM account_memories WHERE owner_id = ? "
            "ORDER BY json_extract(payload, '$.updated_at') DESC, rowid DESC",
            (owner_id,),
        )
        return [AccountMemory.model_validate_json(row[0]) for row in rows]

    def get(self, owner_id: str, memory_id: str) -> AccountMemory | None:
        row = (
            self._pool.get()
            .execute(
                "SELECT payload FROM account_memories WHERE owner_id = ? AND memory_id = ?",
                (owner_id, memory_id),
            )
            .fetchone()
        )
        return AccountMemory.model_validate_json(row[0]) if row else None

    def save(self, memory: AccountMemory, *, expected_revision: int | None = None) -> AccountMemory:
        conn = self._pool.get()
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            return self._save_locked(memory, expected_revision=expected_revision)

    def save_many(self, memories: list[AccountMemory]) -> list[AccountMemory]:
        conn = self._pool.get()
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            return [self._save_locked(memory) for memory in memories]

    def _save_locked(
        self,
        memory: AccountMemory,
        *,
        expected_revision: int | None = None,
        enforce_quota: bool = True,
    ) -> AccountMemory:
        conn = self._pool.get()
        if expected_revision is None and memory.kind == "profile":
            row = conn.execute(
                "SELECT payload FROM account_memories WHERE owner_id=? "
                "AND json_extract(payload, '$.kind')='profile' AND json_extract(payload, '$.profile_field')=?",
                (memory.owner_id, memory.profile_field),
            ).fetchone()
            if row is not None:
                existing = AccountMemory.model_validate_json(row[0])
                stale = memory.updated_at < existing.updated_at
                same = memory.value == existing.value and memory.content == existing.content
                needs_review = (
                    existing.review_at is not None and existing.review_at <= memory.updated_at
                )
                if stale or (same and not needs_review):
                    return existing
                memory = AccountMemory.model_validate(
                    {
                        **memory.model_dump(),
                        "memory_id": existing.memory_id,
                        "created_at": existing.created_at,
                        "revision": existing.revision + 1,
                    }
                )
                expected_revision = existing.revision
        if memory.kind == "profile":
            duplicate = conn.execute(
                "SELECT payload FROM account_memories WHERE owner_id=? AND memory_id<>? "
                "AND json_extract(payload, '$.kind')='profile' AND json_extract(payload, '$.profile_field')=?",
                (memory.owner_id, memory.memory_id, memory.profile_field),
            ).fetchone()
        else:
            duplicate = conn.execute(
                "SELECT payload FROM account_memories WHERE owner_id=? AND memory_id<>? "
                "AND json_extract(payload, '$.kind')=? AND json_extract(payload, '$.content')=?",
                (memory.owner_id, memory.memory_id, memory.kind, memory.content),
            ).fetchone()
        if expected_revision is None:
            if duplicate is not None:
                return AccountMemory.model_validate_json(duplicate[0])
            count = conn.execute(
                "SELECT COUNT(*) FROM account_memories WHERE owner_id=?", (memory.owner_id,)
            ).fetchone()[0]
            if enforce_quota and count >= 200:
                raise ValueError("账号记忆已达200条，请先清理")
            conn.execute(
                "INSERT INTO account_memories VALUES (?, ?, ?, ?)",
                (memory.memory_id, memory.owner_id, memory.revision, memory.model_dump_json()),
            )
        else:
            if duplicate is not None:
                raise AccountMemoryConflict("相同内容或档案字段已存在，请选择要保留的记录")
            cursor = conn.execute(
                "UPDATE account_memories SET revision=?, payload=? "
                "WHERE memory_id=? AND owner_id=? AND revision=?",
                (
                    memory.revision,
                    memory.model_dump_json(),
                    memory.memory_id,
                    memory.owner_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise AccountMemoryConflict("记忆已被修改，请刷新后重试")
        return memory

    def delete(
        self, owner_id: str, memory_id: str, *, expected_revision: int | None = None
    ) -> bool:
        conn = self._pool.get()
        with conn:
            return (
                conn.execute(
                    "DELETE FROM account_memories WHERE owner_id=? AND memory_id=? "
                    "AND (? IS NULL OR revision=?)",
                    (owner_id, memory_id, expected_revision, expected_revision),
                ).rowcount
                == 1
            )

    def clear(self, owner_id: str) -> int:
        conn = self._pool.get()
        with conn:
            return conn.execute(
                "DELETE FROM account_memories WHERE owner_id=?", (owner_id,)
            ).rowcount

    def enabled(self, owner_id: str) -> bool:
        row = (
            self._pool.get()
            .execute(
                "SELECT enabled FROM account_memory_settings WHERE owner_id=?",
                (owner_id,),
            )
            .fetchone()
        )
        return bool(row[0]) if row else False

    def set_enabled(self, owner_id: str, enabled: bool) -> None:
        conn = self._pool.get()
        with conn:
            conn.execute(
                "INSERT INTO account_memory_settings VALUES (?, ?) "
                "ON CONFLICT(owner_id) DO UPDATE SET enabled=excluded.enabled",
                (owner_id, int(enabled)),
            )

    def snapshot(self, run_id: str, owner_id: str, memories: list[AccountMemory]) -> None:
        conn = self._pool.get()
        with conn:
            inserted = conn.execute(
                "INSERT OR IGNORE INTO run_memory_snapshots VALUES (?, ?)", (run_id, owner_id)
            )
            if inserted.rowcount == 0:
                return
            conn.executemany(
                "INSERT INTO run_account_memories VALUES (?, ?, ?, ?)",
                [(run_id, owner_id, item.memory_id, item.revision) for item in memories],
            )

    def references(self, run_id: str, owner_id: str) -> dict[str, int]:
        return dict(
            self._pool.get()
            .execute(
                "SELECT memory_id, revision FROM run_account_memories WHERE run_id=? AND owner_id=?",
                (run_id, owner_id),
            )
            .fetchall()
        )
