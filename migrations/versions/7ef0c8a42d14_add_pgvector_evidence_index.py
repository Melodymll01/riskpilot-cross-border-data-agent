"""add pgvector evidence index

Revision ID: 7ef0c8a42d14
Revises: 0ddb370aee40
Create Date: 2026-08-17 14:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "7ef0c8a42d14"
down_revision: str | Sequence[str] | None = "0ddb370aee40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    dialect_name = op.get_bind().dialect.name
    op.add_column(
        "evidence_chunks",
        sa.Column("search_tokens", sa.Text(), nullable=False, server_default=""),
    )
    op.execute("UPDATE evidence_chunks SET search_tokens = text")

    if dialect_name != "postgresql":
        return

    op.alter_column("evidence_chunks", "search_tokens", server_default=None)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.alter_column(
        "evidence_chunks",
        "embedding",
        existing_type=postgresql.JSONB(),
        type_=Vector(),
        postgresql_using="embedding::text::vector",
    )
    op.execute(
        """
        CREATE INDEX ix_evidence_chunks_embedding_hnsw_2048
        ON evidence_chunks
        USING hnsw ((embedding::halfvec(2048)) halfvec_cosine_ops)
        WHERE vector_dims(embedding) = 2048
        """
    )
    op.execute(
        """
        CREATE INDEX ix_evidence_chunks_search_tokens_fts
        ON evidence_chunks
        USING gin (to_tsvector('simple', search_tokens))
        """
    )


def downgrade() -> None:
    dialect_name = op.get_bind().dialect.name
    if dialect_name == "postgresql":
        op.drop_index(
            "ix_evidence_chunks_search_tokens_fts",
            table_name="evidence_chunks",
        )
        op.drop_index(
            "ix_evidence_chunks_embedding_hnsw_2048",
            table_name="evidence_chunks",
        )
        op.alter_column(
            "evidence_chunks",
            "embedding",
            existing_type=Vector(),
            type_=postgresql.JSONB(),
            postgresql_using="embedding::text::jsonb",
        )

    op.drop_column("evidence_chunks", "search_tokens")
