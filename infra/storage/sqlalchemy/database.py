"""SQLAlchemy 2.x Engine、Session 与事务边界。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


class SqlAlchemyDatabase:
    """每个 Repository 共享同一 Engine；每次调用使用短生命周期事务。"""

    def __init__(
        self,
        database_url: str,
        *,
        engine: Engine | None = None,
        echo: bool = False,
    ) -> None:
        self._engine = engine or _build_engine(database_url, echo=echo)
        self._sessions = sessionmaker(
            bind=self._engine,
            class_=Session,
            expire_on_commit=False,
        )

    @property
    def engine(self) -> Engine:
        return self._engine

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self._sessions.begin() as session:
            yield session

    @contextmanager
    def read_session(self) -> Iterator[Session]:
        with self._sessions() as session:
            yield session

    def ping(self) -> bool:
        try:
            with self._engine.connect() as connection:
                return connection.execute(text("SELECT 1")).scalar_one() == 1
        except Exception:
            return False

    def dispose(self) -> None:
        self._engine.dispose()


def _build_engine(database_url: str, *, echo: bool) -> Engine:
    kwargs: dict[str, Any] = {
        "pool_pre_ping": True,
        "echo": echo,
    }
    if database_url in {"sqlite://", "sqlite:///:memory:"}:
        kwargs.update(
            {
                "connect_args": {"check_same_thread": False},
                "poolclass": StaticPool,
            }
        )
    return create_engine(database_url, **kwargs)
