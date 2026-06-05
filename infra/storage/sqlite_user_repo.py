"""SQLite UserRepoPort 实现。"""

from __future__ import annotations

import time
from typing import Any

from domain.models import Provider, User
from infra.storage._db import SqliteConnectionPool


class SqliteUserRepo:
    """`UserRepoPort` 的 SQLite 实现。"""

    def __init__(self, pool: SqliteConnectionPool) -> None:
        self._pool = pool

    # ── 基础 CRUD ─────────────────────────────────────────────────────────

    def upsert(self, user: User) -> None:
        conn = self._pool.get()
        conn.execute(
            """
            INSERT INTO users
                (user_id, provider, provider_id, email, display_name,
                 avatar_url, created_at, last_active_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                provider       = excluded.provider,
                provider_id    = excluded.provider_id,
                email          = excluded.email,
                display_name   = excluded.display_name,
                avatar_url     = excluded.avatar_url,
                last_active_at = excluded.last_active_at
            """,
            (
                user.user_id,
                user.provider,
                user.provider_id,
                user.email,
                user.display_name,
                user.avatar_url,
                user.created_at,
                user.last_active_at,
            ),
        )
        conn.commit()

    def get(self, user_id: str) -> User | None:
        conn = self._pool.get()
        row = conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row is None:
            return None
        return _row_to_user(row)

    def merge_owner(self, from_id: str, to_id: str) -> int:
        """把 `owner_id == from_id` 的所有 task 迁移到 `to_id`。

        users 表本身保留 `from_id`（不删，避免外部仍持有 token 时的悬挂引用）；
        资源迁移仅涉及 tasks 表（messages / tool_calls / artifacts 通过 task_id 间接归属）。
        """
        if from_id == to_id:
            return 0
        conn = self._pool.get()
        cur = conn.execute(
            "UPDATE tasks SET owner_id = ? WHERE owner_id = ?",
            (to_id, from_id),
        )
        conn.commit()
        return cur.rowcount

    def touch(self, user_id: str) -> None:
        conn = self._pool.get()
        conn.execute(
            "UPDATE users SET last_active_at = ? WHERE user_id = ?",
            (time.time(), user_id),
        )
        conn.commit()


def _row_to_user(row: Any) -> User:
    return User(
        user_id=row["user_id"],
        provider=_validate_provider(row["provider"]),
        provider_id=row["provider_id"],
        email=row["email"],
        display_name=row["display_name"],
        avatar_url=row["avatar_url"],
        created_at=row["created_at"],
        last_active_at=row["last_active_at"],
    )


def _validate_provider(value: str) -> Provider:
    """兼容性校验：DB 里若混入了非法值（旧数据），尽早抛出。"""
    if value not in ("github", "google", "magic_link", "anonymous"):
        msg = f"invalid provider in DB: {value!r}"
        raise ValueError(msg)
    return value  # type: ignore[return-value]
