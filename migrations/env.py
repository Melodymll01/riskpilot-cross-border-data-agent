"""Alembic migration environment。"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from config import Settings
from infra.storage.sqlalchemy.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = config.attributes.get("database_url")
if not isinstance(database_url, str) or not database_url:
    database_url = Settings().database_url
config.set_main_option("sqlalchemy.url", database_url)
target_metadata = Base.metadata
_MANUALLY_COMPARED_INDEXES = {"ix_evidence_chunks_embedding_hnsw_2048"}


def include_object(
    _object: object,
    name: str | None,
    type_: str,
    _reflected: bool,
    _compare_to: object | None,
) -> bool:
    """跳过 Alembic 无法规范化比较的 pgvector 表达式索引。

    该索引仍由显式 migration 创建，并由真实 PostgreSQL schema contract 验证。
    """
    return not (type_ == "index" and name in _MANUALLY_COMPARED_INDEXES)


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
