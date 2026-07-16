"""add static processing job metadata

Revision ID: f9389ed2964e
Revises: 94b0f7ce7782
Create Date: 2026-07-16 19:27:41.682200
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f9389ed2964e"
down_revision: str | None = "94b0f7ce7782"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the schema change."""
    op.add_column(
        "indexing_jobs",
        sa.Column("parsed_file_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "indexing_jobs",
        sa.Column("partial_file_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "indexing_jobs",
        sa.Column(
            "parser_skipped_file_count", sa.Integer(), server_default="0", nullable=False
        ),
    )
    op.add_column(
        "indexing_jobs",
        sa.Column("symbol_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "indexing_jobs",
        sa.Column("chunk_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "indexing_jobs",
        sa.Column(
            "parser_warnings_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    for column in (
        "parsed_file_count",
        "partial_file_count",
        "parser_skipped_file_count",
        "symbol_count",
        "chunk_count",
    ):
        op.create_check_constraint(
            f"ck_indexing_jobs_{column}_nonnegative",
            "indexing_jobs",
            f"{column} >= 0",
        )


def downgrade() -> None:
    """Revert the schema change."""
    for column in (
        "chunk_count",
        "symbol_count",
        "parser_skipped_file_count",
        "partial_file_count",
        "parsed_file_count",
    ):
        op.drop_constraint(
            f"ck_indexing_jobs_{column}_nonnegative",
            "indexing_jobs",
            type_="check",
        )
    op.drop_column("indexing_jobs", "parser_warnings_json")
    op.drop_column("indexing_jobs", "chunk_count")
    op.drop_column("indexing_jobs", "symbol_count")
    op.drop_column("indexing_jobs", "parser_skipped_file_count")
    op.drop_column("indexing_jobs", "partial_file_count")
    op.drop_column("indexing_jobs", "parsed_file_count")
