"""对话历史持久化模块 — 基于 SQLite。"""

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import List, Optional


DB_PATH = "./data/chat_history.db"

_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """每个线程一个 SQLite 连接（sqlite3 不允许跨线程共享连接）。"""
    if not hasattr(_local, "conn"):
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


def init_db():
    """初始化数据库表结构（幂等）。"""
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS conversations (
            id          TEXT PRIMARY KEY,
            title       TEXT NOT NULL DEFAULT '新对话',
            created_at  REAL NOT NULL,
            updated_at  REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS messages (
            id              TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            role            TEXT NOT NULL CHECK(role IN ('user', 'ai')),
            content         TEXT NOT NULL,
            citations       TEXT NOT NULL DEFAULT '[]',
            created_at      REAL NOT NULL,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_messages_conv
            ON messages(conversation_id, created_at);

        CREATE INDEX IF NOT EXISTS idx_conversations_updated
            ON conversations(updated_at DESC);
    """)
    conn.commit()


# ======================== 数据结构 ========================

@dataclass
class MessageRecord:
    id: str
    conversation_id: str
    role: str
    content: str
    citations: list = field(default_factory=list)
    created_at: float = 0.0


@dataclass
class ConversationRecord:
    id: str
    title: str
    created_at: float
    updated_at: float
    messages: List[MessageRecord] = field(default_factory=list)
    message_count: int = 0


# ======================== CRUD ========================

def create_conversation(title: str = "新对话") -> ConversationRecord:
    """创建新对话。"""
    conn = _get_conn()
    now = time.time()
    conv_id = uuid.uuid4().hex[:16]
    conn.execute(
        "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (conv_id, title, now, now),
    )
    conn.commit()
    return ConversationRecord(id=conv_id, title=title, created_at=now, updated_at=now)


def list_conversations(limit: int = 50, offset: int = 0) -> List[ConversationRecord]:
    """列出对话，按最后更新时间倒序。"""
    conn = _get_conn()
    rows = conn.execute(
        """SELECT c.*, COUNT(m.id) AS message_count
           FROM conversations c
           LEFT JOIN messages m ON m.conversation_id = c.id
           GROUP BY c.id
           ORDER BY c.updated_at DESC
           LIMIT ? OFFSET ?""",
        (limit, offset),
    ).fetchall()
    return [
        ConversationRecord(
            id=r["id"], title=r["title"],
            created_at=r["created_at"], updated_at=r["updated_at"],
            message_count=r["message_count"],
        )
        for r in rows
    ]


def get_conversation(conv_id: str) -> Optional[ConversationRecord]:
    """获取对话及其所有消息。"""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conv_id,)).fetchone()
    if not row:
        return None
    conv = ConversationRecord(
        id=row["id"], title=row["title"],
        created_at=row["created_at"], updated_at=row["updated_at"],
    )
    msg_rows = conn.execute(
        "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at",
        (conv_id,),
    ).fetchall()
    conv.messages = [
        MessageRecord(
            id=r["id"], conversation_id=r["conversation_id"],
            role=r["role"], content=r["content"],
            citations=json.loads(r["citations"]),
            created_at=r["created_at"],
        )
        for r in msg_rows
    ]
    conv.message_count = len(conv.messages)
    return conv


def add_message(conv_id: str, role: str, content: str, citations: list = None) -> MessageRecord:
    """向指定对话添加一条消息，并更新对话的 updated_at。"""
    conn = _get_conn()
    now = time.time()
    msg_id = uuid.uuid4().hex[:16]
    citations_json = json.dumps(citations or [], ensure_ascii=False)
    conn.execute(
        "INSERT INTO messages (id, conversation_id, role, content, citations, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (msg_id, conv_id, role, content, citations_json, now),
    )
    conn.execute(
        "UPDATE conversations SET updated_at = ? WHERE id = ?",
        (now, conv_id),
    )
    conn.commit()
    return MessageRecord(
        id=msg_id, conversation_id=conv_id,
        role=role, content=content,
        citations=citations or [], created_at=now,
    )


def update_conversation_title(conv_id: str, title: str) -> bool:
    """更新对话标题。"""
    conn = _get_conn()
    cursor = conn.execute(
        "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
        (title, time.time(), conv_id),
    )
    conn.commit()
    return cursor.rowcount > 0


def delete_conversation(conv_id: str) -> bool:
    """删除对话及其所有消息（CASCADE）。"""
    conn = _get_conn()
    cursor = conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
    conn.commit()
    return cursor.rowcount > 0


# 模块加载时自动初始化
init_db()
