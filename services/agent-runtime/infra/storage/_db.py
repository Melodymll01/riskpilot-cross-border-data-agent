"""Thread-local SQLite connections for Python-owned runtime artifacts only."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path


class SqliteConnectionPool:
    def __init__(self, db_path: str) -> None:
        if db_path == ":memory:":
            self._path = ":memory:"
        else:
            path = Path(db_path).expanduser().resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            self._path = str(path)
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
