"""Lifecycle wrapper around LangGraph's lightweight SQLite checkpointer."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver


class SqliteConversationCheckpointer:
    def __init__(self, path: str) -> None:
        checkpoint_path = Path(path)
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            checkpoint_path,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self.saver = SqliteSaver(self._connection)
        self.saver.setup()

    def close(self) -> None:
        self._connection.close()
