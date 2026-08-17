"""Alembic 核心 schema 迁移测试。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

_POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")


def _config(database_url: str, *, output_buffer=None) -> Config:
    config = Config("alembic.ini", output_buffer=output_buffer)
    config.attributes["database_url"] = database_url
    return config


def test_upgrade_downgrade_upgrade_is_reversible(tmp_path: Path) -> None:
    database_path = tmp_path / "migration.sqlite3"
    config = _config(f"sqlite:///{database_path}")

    command.upgrade(config, "head")
    command.downgrade(config, "base")
    command.upgrade(config, "head")

    from sqlalchemy import create_engine

    inspector = inspect(create_engine(f"sqlite:///{database_path}"))
    tables = set(inspector.get_table_names())
    indexes = {item["name"] for item in inspector.get_indexes("agent_runs")}
    assert {
        "alembic_version",
        "workspaces",
        "compliance_cases",
        "documents",
        "case_facts",
        "assessments",
        "agent_runs",
        "run_events",
    }.issubset(tables)
    assert "uq_agent_runs_active_case_workflow" in indexes


def test_postgres_offline_sql_contains_production_types_and_indexes(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "postgres.sql"
    with output_path.open("w", encoding="utf-8") as output:
        config = _config(
            "postgresql+psycopg://riskpilot:riskpilot@127.0.0.1:5432/riskpilot",
            output_buffer=output,
        )
        command.upgrade(config, "head", sql=True)

    sql = output_path.read_text(encoding="utf-8")
    assert "JSONB" in sql
    assert "CREATE UNIQUE INDEX uq_agent_runs_active_case_workflow" in sql
    assert "WHERE status IN" in sql
    assert "CREATE EXTENSION IF NOT EXISTS vector" in sql
    assert "ALTER COLUMN embedding TYPE VECTOR" in sql
    assert "embedding::halfvec(2048)" in sql
    assert "halfvec_cosine_ops" in sql
    assert "to_tsvector('simple', search_tokens)" in sql


@pytest.mark.skipif(
    not _POSTGRES_URL,
    reason="需要 TEST_POSTGRES_URL 验证 Alembic 真实 pgvector schema",
)
def test_live_postgres_migration_has_pgvector_extension_and_indexes() -> None:
    engine = create_engine(_POSTGRES_URL)
    try:
        with engine.connect() as connection:
            extension_version = connection.scalar(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )
            index_definitions: dict[str, str] = {}
            for name, definition in connection.execute(
                text(
                    """
                    SELECT indexname, indexdef
                    FROM pg_indexes
                    WHERE tablename = 'evidence_chunks'
                      AND indexname IN (
                          'ix_evidence_chunks_embedding_hnsw_2048',
                          'ix_evidence_chunks_search_tokens_fts'
                      )
                    """
                )
            ):
                index_definitions[str(name)] = str(definition)
        assert extension_version is not None
        assert "USING hnsw" in index_definitions["ix_evidence_chunks_embedding_hnsw_2048"]
        assert "halfvec(2048)" in index_definitions["ix_evidence_chunks_embedding_hnsw_2048"]
        assert "halfvec_cosine_ops" in index_definitions["ix_evidence_chunks_embedding_hnsw_2048"]
        assert (
            "vector_dims(embedding) = 2048"
            in index_definitions["ix_evidence_chunks_embedding_hnsw_2048"]
        )
        assert "USING gin" in index_definitions["ix_evidence_chunks_search_tokens_fts"]
        assert "to_tsvector" in index_definitions["ix_evidence_chunks_search_tokens_fts"]
    finally:
        engine.dispose()
