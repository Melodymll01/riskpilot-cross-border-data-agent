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


CREATE TABLE IF NOT EXISTS chat_documents (
    document_id        TEXT PRIMARY KEY,
    owner_id       TEXT NOT NULL,
    logical_name       TEXT NOT NULL,
    document_type      TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'uploaded',
    created_by         TEXT NOT NULL,
    current_version_id TEXT,
    created_at         REAL NOT NULL,
    updated_at         REAL NOT NULL
);


CREATE INDEX IF NOT EXISTS idx_chat_documents_owner
    ON chat_documents(owner_id, updated_at DESC);


CREATE TABLE IF NOT EXISTS chat_document_versions (
    version_id      TEXT PRIMARY KEY,
    document_id     TEXT NOT NULL,
    version_number  INTEGER NOT NULL,
    object_key      TEXT NOT NULL UNIQUE,
    sha256          TEXT NOT NULL,
    mime_type       TEXT NOT NULL,
    size_bytes      INTEGER NOT NULL,
    parser_version  TEXT NOT NULL DEFAULT '',
    page_count      INTEGER,
    created_at      REAL NOT NULL,
    UNIQUE (document_id, version_number),
    FOREIGN KEY (document_id) REFERENCES chat_documents(document_id) ON DELETE CASCADE
);


CREATE INDEX IF NOT EXISTS idx_chat_document_versions_document
    ON chat_document_versions(document_id, version_number DESC);


CREATE TABLE IF NOT EXISTS chat_processing_jobs (
    job_id               TEXT PRIMARY KEY,
    document_version_id  TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'queued',
    current_stage        TEXT NOT NULL DEFAULT 'validate',
    progress             REAL NOT NULL DEFAULT 0,
    error_code           TEXT,
    error_message        TEXT,
    retry_count          INTEGER NOT NULL DEFAULT 0,
    revision             INTEGER NOT NULL DEFAULT 0,
    created_at           REAL NOT NULL,
    updated_at           REAL NOT NULL,
    started_at           REAL,
    completed_at         REAL,
    FOREIGN KEY (document_version_id) REFERENCES chat_document_versions(version_id) ON DELETE CASCADE
);


CREATE INDEX IF NOT EXISTS idx_chat_processing_jobs_version
    ON chat_processing_jobs(document_version_id, created_at DESC);


CREATE TABLE IF NOT EXISTS chat_document_parse_snapshots (
    snapshot_id          TEXT PRIMARY KEY,
    document_version_id  TEXT NOT NULL UNIQUE,
    payload_json         TEXT NOT NULL,
    parsed_at            REAL NOT NULL,
    FOREIGN KEY (document_version_id) REFERENCES chat_document_versions(version_id) ON DELETE CASCADE
);


CREATE TABLE IF NOT EXISTS chat_evidence_chunks (
    chunk_id              TEXT PRIMARY KEY,
    owner_id          TEXT NOT NULL,
    document_id           TEXT NOT NULL,
    document_version_id   TEXT NOT NULL,
    page_number           INTEGER NOT NULL,
    chunk_index           INTEGER NOT NULL,
    text                  TEXT NOT NULL,
    source_sha256         TEXT NOT NULL,
    embedding_json        TEXT NOT NULL,
    created_at            REAL NOT NULL,
    UNIQUE (document_version_id, page_number, chunk_index),
    FOREIGN KEY (document_id) REFERENCES chat_documents(document_id) ON DELETE CASCADE,
    FOREIGN KEY (document_version_id) REFERENCES chat_document_versions(version_id) ON DELETE CASCADE
);


CREATE INDEX IF NOT EXISTS idx_chat_evidence_chunks_scope ON chat_evidence_chunks(owner_id, document_id);


CREATE TABLE IF NOT EXISTS tasks (
    task_id          TEXT PRIMARY KEY,
    owner_id         TEXT NOT NULL,
    title            TEXT NOT NULL DEFAULT '',
    state            TEXT NOT NULL DEFAULT 'planning',
    user_goal        TEXT NOT NULL DEFAULT '',
    collected_facts  TEXT NOT NULL DEFAULT '{}',
    created_at       REAL NOT NULL,
    updated_at       REAL NOT NULL
);


CREATE INDEX IF NOT EXISTS idx_tasks_owner
    ON tasks(owner_id, updated_at DESC);


CREATE TABLE IF NOT EXISTS task_attachments (
    task_id      TEXT NOT NULL,
    document_id  TEXT NOT NULL,
    selected_at  REAL NOT NULL,
    selection_order INTEGER NOT NULL,
    PRIMARY KEY (task_id, document_id),
    FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE,
    FOREIGN KEY (document_id) REFERENCES chat_documents(document_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_task_attachments_task
    ON task_attachments(task_id, selected_at);


CREATE TABLE IF NOT EXISTS conversation_runs (
    run_id              TEXT PRIMARY KEY,
    thread_id           TEXT NOT NULL UNIQUE,
    owner_id            TEXT NOT NULL,
    task_id             TEXT NOT NULL,
    question            TEXT NOT NULL,
    input_message_id    TEXT NOT NULL,
    attachment_doc_ids  TEXT NOT NULL DEFAULT '[]',
    status              TEXT NOT NULL,
    stage               TEXT NOT NULL,
    evidence_json       TEXT NOT NULL DEFAULT '[]',
    verified_citation_ids TEXT NOT NULL DEFAULT '[]',
    verified_context_message_ids TEXT NOT NULL DEFAULT '[]',
    clarification_fields TEXT NOT NULL DEFAULT '[]',
    max_duration_seconds REAL NOT NULL DEFAULT 120,
    remaining_seconds REAL NOT NULL DEFAULT 120,
    deadline_at REAL,
    max_model_calls INTEGER NOT NULL DEFAULT 12,
    max_tool_calls INTEGER NOT NULL DEFAULT 6,
    model_call_count    INTEGER NOT NULL DEFAULT 0,
    tool_call_count     INTEGER NOT NULL DEFAULT 0,
    model_retry_count   INTEGER NOT NULL DEFAULT 0,
    tool_retry_count    INTEGER NOT NULL DEFAULT 0,
    input_tokens        INTEGER,
    output_tokens       INTEGER,
    usage_reported_model_calls INTEGER NOT NULL DEFAULT 0,
    answer_message_id   TEXT,
    error_type          TEXT,
    end_reason          TEXT,
    cancel_requested_at REAL,
    finished_at         REAL,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE,
    FOREIGN KEY (input_message_id) REFERENCES messages(msg_id) ON DELETE CASCADE,
    FOREIGN KEY (answer_message_id) REFERENCES messages(msg_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_conversation_runs_task
    ON conversation_runs(task_id, updated_at DESC);


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
    run_id        TEXT,
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


CREATE TABLE IF NOT EXISTS message_feedback (
    msg_id     TEXT PRIMARY KEY,
    task_id    TEXT NOT NULL,
    owner_id   TEXT NOT NULL,
    rating     TEXT NOT NULL CHECK(rating IN ('up', 'down')),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);


CREATE INDEX IF NOT EXISTS idx_feedback_owner
    ON message_feedback(owner_id);


CREATE INDEX IF NOT EXISTS idx_feedback_rating
    ON message_feedback(rating);"""


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
            _apply_schema_upgrades(conn)
            conn.commit()
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def ping(self) -> bool:
        try:
            row = self.get().execute("SELECT 1").fetchone()
        except sqlite3.Error:
            return False
        return row is not None and row[0] == 1


def _apply_schema_upgrades(conn: sqlite3.Connection) -> None:
    """Only additive upgrades for tables retained from the phase-1 baseline."""

    tool_columns = {row[1] for row in conn.execute("PRAGMA table_info(tool_calls)")}
    if "run_id" not in tool_columns:
        conn.execute("ALTER TABLE tool_calls ADD COLUMN run_id TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_calls_run ON tool_calls(run_id, created_at)")
    run_columns = {row[1] for row in conn.execute("PRAGMA table_info(conversation_runs)")}
    if run_columns and "verified_citation_ids" not in run_columns:
        conn.execute(
            "ALTER TABLE conversation_runs ADD COLUMN verified_citation_ids TEXT NOT NULL DEFAULT '[]'"
        )
    if run_columns and "verified_context_message_ids" not in run_columns:
        conn.execute(
            "ALTER TABLE conversation_runs ADD COLUMN verified_context_message_ids "
            "TEXT NOT NULL DEFAULT '[]'"
        )
    if run_columns and "model_call_count" not in run_columns:
        conn.execute(
            "ALTER TABLE conversation_runs ADD COLUMN model_call_count INTEGER NOT NULL DEFAULT 0"
        )
    if run_columns and "clarification_fields" not in run_columns:
        conn.execute(
            "ALTER TABLE conversation_runs ADD COLUMN clarification_fields TEXT NOT NULL DEFAULT '[]'"
        )
    if run_columns and "tool_call_count" not in run_columns:
        conn.execute(
            "ALTER TABLE conversation_runs ADD COLUMN tool_call_count INTEGER NOT NULL DEFAULT 0"
        )
    run_column_upgrades = {
        "max_duration_seconds": "REAL NOT NULL DEFAULT 120",
        "remaining_seconds": "REAL NOT NULL DEFAULT 120",
        "deadline_at": "REAL",
        "max_model_calls": "INTEGER NOT NULL DEFAULT 12",
        "max_tool_calls": "INTEGER NOT NULL DEFAULT 6",
        "model_retry_count": "INTEGER NOT NULL DEFAULT 0",
        "tool_retry_count": "INTEGER NOT NULL DEFAULT 0",
        "input_tokens": "INTEGER",
        "output_tokens": "INTEGER",
        "usage_reported_model_calls": "INTEGER NOT NULL DEFAULT 0",
        "end_reason": "TEXT",
        "cancel_requested_at": "REAL",
        "finished_at": "REAL",
    }
    for column, definition in run_column_upgrades.items():
        if run_columns and column not in run_columns:
            conn.execute(f"ALTER TABLE conversation_runs ADD COLUMN {column} {definition}")
    attachment_columns = {row[1] for row in conn.execute("PRAGMA table_info(task_attachments)")}
    if attachment_columns and "selection_order" not in attachment_columns:
        conn.execute(
            "ALTER TABLE task_attachments ADD COLUMN selection_order INTEGER NOT NULL DEFAULT 0"
        )
