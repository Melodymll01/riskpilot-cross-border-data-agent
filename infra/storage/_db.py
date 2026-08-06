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

CREATE TABLE IF NOT EXISTS workspaces (
    workspace_id TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'active',
    created_by   TEXT NOT NULL,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_workspaces_updated
    ON workspaces(updated_at DESC);

CREATE TABLE IF NOT EXISTS workspace_memberships (
    workspace_id TEXT NOT NULL,
    user_id      TEXT NOT NULL,
    role         TEXT NOT NULL,
    joined_at    REAL NOT NULL,
    PRIMARY KEY (workspace_id, user_id),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_workspace_memberships_user
    ON workspace_memberships(user_id, joined_at DESC);

CREATE TABLE IF NOT EXISTS compliance_cases (
    case_id               TEXT PRIMARY KEY,
    workspace_id          TEXT NOT NULL,
    title                 TEXT NOT NULL,
    description           TEXT NOT NULL DEFAULT '',
    jurisdiction          TEXT NOT NULL DEFAULT 'CN',
    scenario_type         TEXT NOT NULL DEFAULT '',
    assessment_date       TEXT,
    status                TEXT NOT NULL DEFAULT 'draft',
    owner_id              TEXT NOT NULL,
    reviewer_id           TEXT,
    active_assessment_id  TEXT,
    created_at            REAL NOT NULL,
    updated_at            REAL NOT NULL,
    FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_compliance_cases_workspace
    ON compliance_cases(workspace_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS documents (
    document_id        TEXT PRIMARY KEY,
    workspace_id       TEXT NOT NULL,
    logical_name       TEXT NOT NULL,
    document_type      TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'uploaded',
    created_by         TEXT NOT NULL,
    current_version_id TEXT,
    created_at         REAL NOT NULL,
    updated_at         REAL NOT NULL,
    FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_documents_workspace
    ON documents(workspace_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS document_versions (
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
    FOREIGN KEY (document_id) REFERENCES documents(document_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_document_versions_document
    ON document_versions(document_id, version_number DESC);

CREATE TABLE IF NOT EXISTS case_documents (
    case_id      TEXT NOT NULL,
    document_id  TEXT NOT NULL,
    purpose      TEXT NOT NULL DEFAULT '',
    added_by     TEXT NOT NULL,
    added_at     REAL NOT NULL,
    PRIMARY KEY (case_id, document_id),
    FOREIGN KEY (case_id) REFERENCES compliance_cases(case_id) ON DELETE CASCADE,
    FOREIGN KEY (document_id) REFERENCES documents(document_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_case_documents_document
    ON case_documents(document_id);

CREATE TABLE IF NOT EXISTS processing_jobs (
    job_id               TEXT PRIMARY KEY,
    document_version_id  TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'queued',
    current_stage        TEXT NOT NULL DEFAULT 'validate',
    progress             REAL NOT NULL DEFAULT 0,
    error_code           TEXT,
    error_message        TEXT,
    retry_count          INTEGER NOT NULL DEFAULT 0,
    created_at           REAL NOT NULL,
    updated_at           REAL NOT NULL,
    started_at           REAL,
    completed_at         REAL,
    FOREIGN KEY (document_version_id) REFERENCES document_versions(version_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_processing_jobs_version
    ON processing_jobs(document_version_id, created_at DESC);

CREATE TABLE IF NOT EXISTS document_parse_snapshots (
    snapshot_id          TEXT PRIMARY KEY,
    document_version_id  TEXT NOT NULL UNIQUE,
    payload_json         TEXT NOT NULL,
    parsed_at            REAL NOT NULL,
    FOREIGN KEY (document_version_id) REFERENCES document_versions(version_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS evidence_chunks (
    chunk_id              TEXT PRIMARY KEY,
    workspace_id          TEXT NOT NULL,
    case_id               TEXT NOT NULL,
    document_id           TEXT NOT NULL,
    document_version_id   TEXT NOT NULL,
    page_number           INTEGER NOT NULL,
    chunk_index           INTEGER NOT NULL,
    text                  TEXT NOT NULL,
    source_sha256         TEXT NOT NULL,
    embedding_json        TEXT NOT NULL,
    created_at            REAL NOT NULL,
    UNIQUE (case_id, document_version_id, page_number, chunk_index),
    FOREIGN KEY (case_id) REFERENCES compliance_cases(case_id) ON DELETE CASCADE,
    FOREIGN KEY (document_id) REFERENCES documents(document_id) ON DELETE CASCADE,
    FOREIGN KEY (document_version_id) REFERENCES document_versions(version_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_evidence_chunks_scope
    ON evidence_chunks(workspace_id, case_id, document_version_id);

CREATE TABLE IF NOT EXISTS case_facts (
    fact_id        TEXT PRIMARY KEY,
    case_id        TEXT NOT NULL,
    field_name     TEXT NOT NULL,
    value_json     TEXT,
    status         TEXT NOT NULL,
    source_type    TEXT NOT NULL,
    confidence     REAL NOT NULL,
    criticality    TEXT NOT NULL,
    version        INTEGER NOT NULL,
    created_by     TEXT NOT NULL,
    confirmed_by   TEXT,
    confirmed_at   REAL,
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL,
    FOREIGN KEY (case_id) REFERENCES compliance_cases(case_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_case_facts_case
    ON case_facts(case_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS case_fact_versions (
    fact_id        TEXT NOT NULL,
    version        INTEGER NOT NULL,
    payload_json   TEXT NOT NULL,
    created_at     REAL NOT NULL,
    PRIMARY KEY (fact_id, version),
    FOREIGN KEY (fact_id) REFERENCES case_facts(fact_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS case_fact_evidence (
    evidence_id         TEXT PRIMARY KEY,
    case_id             TEXT NOT NULL,
    fact_id             TEXT NOT NULL,
    fact_version        INTEGER NOT NULL,
    document_id         TEXT NOT NULL,
    document_version_id TEXT NOT NULL,
    page_number         INTEGER NOT NULL,
    quote               TEXT NOT NULL,
    start_offset        INTEGER,
    end_offset          INTEGER,
    confidence          REAL NOT NULL,
    created_at          REAL NOT NULL,
    FOREIGN KEY (case_id) REFERENCES compliance_cases(case_id) ON DELETE CASCADE,
    FOREIGN KEY (fact_id, fact_version)
        REFERENCES case_fact_versions(fact_id, version) ON DELETE CASCADE,
    FOREIGN KEY (document_id) REFERENCES documents(document_id) ON DELETE CASCADE,
    FOREIGN KEY (document_version_id) REFERENCES document_versions(version_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_case_fact_evidence_fact
    ON case_fact_evidence(fact_id, fact_version);

CREATE TABLE IF NOT EXISTS policy_rules (
    workspace_id         TEXT NOT NULL,
    rule_id              TEXT NOT NULL,
    ruleset_version      TEXT NOT NULL,
    jurisdiction         TEXT NOT NULL,
    effective_from       TEXT NOT NULL,
    effective_to         TEXT,
    status               TEXT NOT NULL,
    required_fact_fields TEXT NOT NULL,
    condition_json       TEXT NOT NULL,
    result_json          TEXT NOT NULL,
    source_clause_ids    TEXT NOT NULL,
    PRIMARY KEY (workspace_id, rule_id, ruleset_version),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_policy_rules_lookup
    ON policy_rules(workspace_id, ruleset_version, jurisdiction, status, effective_from);

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

CREATE TABLE IF NOT EXISTS memory_settings (
    owner_id          TEXT PRIMARY KEY,
    use_saved_memory  INTEGER NOT NULL DEFAULT 1,
    updated_at        REAL NOT NULL
);

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
    ON message_feedback(rating);
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
