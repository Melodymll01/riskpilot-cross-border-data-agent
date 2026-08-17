"""add processing job revision

Revision ID: c312b95fd8a1
Revises: 7ef0c8a42d14
Create Date: 2026-08-17 13:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c312b95fd8a1"
down_revision: str | Sequence[str] | None = "7ef0c8a42d14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "processing_jobs",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column("processing_jobs", "revision", server_default=None)


def downgrade() -> None:
    op.drop_column("processing_jobs", "revision")
