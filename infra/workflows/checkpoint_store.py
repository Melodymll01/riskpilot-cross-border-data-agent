"""LangGraph checkpoint store 工厂与资源生命周期。"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Literal, cast

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

CheckpointBackend = Literal["sqlite", "postgres"]


class CheckpointStore:
    """保持 checkpoint 连接生命周期，并在首次使用时完成数据库初始化。"""

    def __init__(
        self,
        *,
        backend: CheckpointBackend,
        sqlite_path: str | None = None,
        database_url: str | None = None,
    ) -> None:
        self._backend = backend
        self._sqlite_path = sqlite_path
        self._database_url = database_url
        self._resource: sqlite3.Connection | ConnectionPool[Any] | None = None
        self._saver: Any | None = None
        self._lock = threading.Lock()

    @property
    def saver(self) -> Any:
        if self._saver is not None:
            return self._saver
        with self._lock:
            if self._saver is None:
                self._saver = self._build()
        return self._saver

    def close(self) -> None:
        with self._lock:
            resource = self._resource
            self._resource = None
            self._saver = None
        if resource is not None:
            resource.close()

    def _build(self) -> Any:
        if self._backend == "postgres":
            if not self._database_url:
                raise ValueError("PostgreSQL checkpoint 必须配置 DATABASE_URL")
            pool: ConnectionPool[Any] = ConnectionPool(
                _psycopg_dsn(self._database_url),
                kwargs={
                    "autocommit": True,
                    "prepare_threshold": 0,
                    "row_factory": dict_row,
                },
                min_size=1,
                max_size=10,
                open=True,
            )
            pool.wait()
            postgres_saver = PostgresSaver(
                cast("Any", pool),
                serde=JsonPlusSerializer(pickle_fallback=False),
            )
            postgres_saver.setup()
            self._resource = pool
            return postgres_saver
        if self._sqlite_path is None:
            raise ValueError("SQLite checkpoint 必须配置数据库路径")
        connection_target = _sqlite_target(self._sqlite_path)
        connection = sqlite3.connect(connection_target, check_same_thread=False)
        sqlite_saver = SqliteSaver(
            connection,
            serde=JsonPlusSerializer(pickle_fallback=False),
        )
        sqlite_saver.setup()
        self._resource = connection
        return sqlite_saver


def _sqlite_target(value: str) -> str:
    if value == ":memory:":
        return value
    path = Path(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def _psycopg_dsn(value: str) -> str:
    if value.startswith("postgresql+psycopg://"):
        return "postgresql://" + value.removeprefix("postgresql+psycopg://")
    if value.startswith("postgresql://"):
        return value
    raise ValueError("PostgreSQL checkpoint DATABASE_URL 必须使用 PostgreSQL URL")
