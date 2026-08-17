"""Alembic 核心 schema 迁移测试。"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect


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


def test_postgres_offline_sql_contains_jsonb_and_partial_unique_index(
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
