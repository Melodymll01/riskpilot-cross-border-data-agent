"""共用 SQLite 工具：连接池（线程局部）+ schema 初始化。

UserRepo / TaskRepo 共享同一数据库与连接管理；调用方通过传入 `db_path` 注入位置，
默认 `./data/app.db`，测试可指向 `:memory:` 或临时文件。
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

# 一份 schema 覆盖所有表，可幂等执行。
_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id        TEXT PRIMARY KEY,
    provider       TEXT NOT NULL,
    provider_id    TEXT NOT NULL,
    email          TEXT,
    display_name   TEXT NOT NULL,
    avatar_url     TEXT,
    created_at     REAL NOT NULL,
    last_active_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_users_provider
    ON users(provider, provider_id);

CREATE TABLE IF NOT EXISTS tasks (
    task_id          TEXT PRIMARY KEY,
    owner_id         TEXT NOT NULL,
    title            TEXT NOT NULL DEFAULT '',
    state            TEXT NOT NULL DEFAULT 'planning',
    mode             TEXT NOT NULL DEFAULT 'qa',
    user_goal        TEXT NOT NULL DEFAULT '',
    collected_facts  TEXT NOT NULL DEFAULT '{}',
    created_at       REAL NOT NULL,
    updated_at       REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_owner
    ON tasks(owner_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    msg_id        TEXT PRIMARY KEY,
    task_id       TEXT NOT NULL,
    role          TEXT NOT NULL,
    content       TEXT NOT NULL,
    tool_call_id  TEXT,
    citations     TEXT NOT NULL DEFAULT '[]',
    created_at    REAL NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_task
    ON messages(task_id, created_at);

CREATE TABLE IF NOT EXISTS tool_calls (
    tool_call_id  TEXT PRIMARY KEY,
    task_id       TEXT NOT NULL,
    tool_name     TEXT NOT NULL,
    input_json    TEXT NOT NULL DEFAULT '{}',
    output_json   TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',
    duration_ms   INTEGER,
    created_at    REAL NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tool_calls_task
    ON tool_calls(task_id, created_at);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id    TEXT PRIMARY KEY,
    task_id        TEXT NOT NULL,
    artifact_type  TEXT NOT NULL,
    payload_json   TEXT NOT NULL DEFAULT '{}',
    created_at     REAL NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_artifacts_task
    ON artifacts(task_id, created_at);

CREATE TABLE IF NOT EXISTS task_summaries (
    task_id       TEXT PRIMARY KEY,
    owner_id      TEXT NOT NULL,
    summary       TEXT NOT NULL DEFAULT '',
    msg_watermark INTEGER NOT NULL DEFAULT 0,
    updated_at    REAL NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_task_summaries_owner
    ON task_summaries(owner_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS consolidation_state (
    task_id       TEXT PRIMARY KEY,
    owner_id      TEXT NOT NULL,
    msg_watermark INTEGER NOT NULL DEFAULT 0,
    updated_at    REAL NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_consolidation_state_owner
    ON consolidation_state(owner_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS profiles (
    owner_id   TEXT PRIMARY KEY,
    facts      TEXT NOT NULL DEFAULT '{}',
    updated_at REAL NOT NULL
);
"""


class SqliteConnectionPool:
    """线程局部 SQLite 连接池。

    sqlite3.Connection 不允许跨线程共享；每个线程首次访问时建立独立连接，
    并执行幂等的 schema 初始化。memory DB 共享方式按 path 区分。
    """

    def __init__(self, db_path: str) -> None:
        # 把相对路径解析为绝对路径，避免不同模块按 cwd 解析出不一致的 DB。
        if db_path == ":memory:":
            self._path = ":memory:"
        else:
            p = Path(db_path).expanduser().resolve()
            p.parent.mkdir(parents=True, exist_ok=True)
            self._path = str(p)
        self._local = threading.local()

    @property
    def path(self) -> str:
        return self._path

    def get(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(_SCHEMA)
            _apply_incremental_migrations(conn)
            conn.commit()
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None


# ── 轻量幂等 migration ──────────────────────────────────────────────────
# `CREATE TABLE IF NOT EXISTS` 对存在的表不会补列，因此后续新增字段需要在这里
# 显式 ALTER。每段都做 PRAGMA 检查，已存在则跳过。


def _apply_incremental_migrations(conn: sqlite3.Connection) -> None:
    # tasks.mode（Step 012-tail：三业务模式）
    cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    if "mode" not in cols:
        conn.execute(
            "ALTER TABLE tasks ADD COLUMN mode TEXT NOT NULL DEFAULT 'qa'"
        )
