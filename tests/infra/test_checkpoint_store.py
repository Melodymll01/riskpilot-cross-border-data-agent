"""LangGraph checkpoint backend 选择与 DSN 契约测试。"""

from __future__ import annotations

import pytest

from infra.workflows.checkpoint_store import CheckpointStore, _psycopg_dsn


def test_postgres_store_is_lazy_until_first_saver_access() -> None:
    store = CheckpointStore(
        backend="postgres",
        database_url="postgresql+psycopg://user:pass@127.0.0.1:1/riskpilot",
    )

    store.close()


def test_sqlalchemy_postgres_url_is_normalized_for_psycopg() -> None:
    assert (
        _psycopg_dsn("postgresql+psycopg://riskpilot:secret@postgres:5432/riskpilot")
        == "postgresql://riskpilot:secret@postgres:5432/riskpilot"
    )

    with pytest.raises(ValueError, match="PostgreSQL URL"):
        _psycopg_dsn("sqlite:///data.db")
