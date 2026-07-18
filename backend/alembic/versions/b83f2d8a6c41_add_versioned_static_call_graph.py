"""Add validated, version-scoped static call-graph persistence.

Revision ID: b83f2d8a6c41
Revises: d06a6455fcd7
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b83f2d8a6c41"
down_revision: str | None = "d06a6455fcd7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_call_edges_call_resolution_type"), "call_edges", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_call_edges_call_resolution_type"),
        "call_edges",
        "resolution_type IN ('exact_same_file', 'exact_direct_import', "
        "'qualified_module', 'probable_method', 'ambiguous', 'unresolved')",
    )
    op.add_column("call_edges", sa.Column("call_end_line", sa.Integer(), nullable=True))
    op.add_column("call_edges", sa.Column("call_expression", sa.String(2048), nullable=True))
    op.add_column("call_edges", sa.Column("call_site_fingerprint", sa.String(64), nullable=True))
    op.execute(
        "UPDATE call_edges SET call_end_line = call_line, "
        "call_expression = COALESCE(unresolved_callee_name, '<resolved>'), "
        "call_site_fingerprint = md5(id::text || ':' || call_line::text)"
    )
    op.alter_column("call_edges", "call_end_line", nullable=False)
    op.alter_column("call_edges", "call_expression", nullable=False)
    op.alter_column("call_edges", "call_site_fingerprint", nullable=False)
    op.create_check_constraint(
        "call_line_range_ordered", "call_edges", "call_end_line >= call_line"
    )
    op.create_unique_constraint(
        "uq_call_edges_repository_version_site",
        "call_edges",
        ["repository_id", "index_version", "call_site_fingerprint"],
    )

    _add_graph_counts("indexing_jobs")
    _add_graph_counts("repository_index_builds")
    op.add_column(
        "repository_index_builds", sa.Column("graph_fingerprint", sa.String(64), nullable=True)
    )
    op.add_column(
        "repository_index_builds",
        sa.Column("graph_validated", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("repository_index_builds", "graph_validated")
    op.drop_column("repository_index_builds", "graph_fingerprint")
    _drop_graph_counts("repository_index_builds")
    _drop_graph_counts("indexing_jobs")

    op.drop_constraint("uq_call_edges_repository_version_site", "call_edges", type_="unique")
    op.drop_constraint("call_line_range_ordered", "call_edges", type_="check")
    op.drop_column("call_edges", "call_site_fingerprint")
    op.drop_column("call_edges", "call_expression")
    op.drop_column("call_edges", "call_end_line")
    op.execute(
        "UPDATE call_edges SET resolution_type = 'unresolved', confidence = 'low', "
        "callee_symbol_id = NULL WHERE resolution_type = 'ambiguous'"
    )
    op.drop_constraint(
        op.f("ck_call_edges_call_resolution_type"), "call_edges", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_call_edges_call_resolution_type"),
        "call_edges",
        "resolution_type IN ('exact_same_file', 'exact_direct_import', "
        "'qualified_module', 'probable_method', 'unresolved')",
    )


def _add_graph_counts(table: str) -> None:
    for column in (
        "call_site_count",
        "exact_edge_count",
        "ambiguous_edge_count",
        "unresolved_call_count",
        "graph_warning_count",
    ):
        op.add_column(
            table,
            sa.Column(column, sa.Integer(), server_default="0", nullable=False),
        )
        op.create_check_constraint(f"{column}_nonnegative", table, f"{column} >= 0")


def _drop_graph_counts(table: str) -> None:
    for column in reversed(
        (
            "call_site_count",
            "exact_edge_count",
            "ambiguous_edge_count",
            "unresolved_call_count",
            "graph_warning_count",
        )
    ):
        op.drop_constraint(f"{column}_nonnegative", table, type_="check")
        op.drop_column(table, column)
