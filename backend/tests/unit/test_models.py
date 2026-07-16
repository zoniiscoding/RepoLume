"""Foundational relational metadata and tenant-scope invariants."""

from typing import cast

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, Table, UniqueConstraint

from app.db import models  # noqa: F401
from app.db.base import Base
from app.db.models.call_edge import CallEdge
from app.db.models.enums import RepositoryIndexingStatus

EXPECTED_TABLES = {
    "alembic_version",
    "call_edges",
    "chat_messages",
    "chat_sessions",
    "github_installations",
    "indexing_jobs",
    "installation_members",
    "oauth_states",
    "refresh_tokens",
    "repositories",
    "repository_index_builds",
    "symbol_definitions",
    "usage_records",
    "users",
    "webhook_deliveries",
}


def test_metadata_contains_every_foundational_application_table() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES - {"alembic_version"}


def test_repository_states_match_product_specification() -> None:
    assert {state.value for state in RepositoryIndexingStatus} == {
        "not_indexed",
        "queued",
        "cloning",
        "discovering",
        "parsing",
        "embedding",
        "building_graph",
        "finalizing",
        "complete",
        "failed",
        "deleting",
        "access_revoked",
    }


def test_installation_membership_is_unique_per_user_and_installation() -> None:
    table = Base.metadata.tables["installation_members"]
    constraints = {constraint.name for constraint in table.constraints}
    assert "uq_installation_members_installation_user" in constraints


def test_repository_identity_is_scoped_to_installation() -> None:
    table = Base.metadata.tables["repositories"]
    unique_constraints = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("installation_id", "github_repository_id") in unique_constraints
    assert ("installation_id", "github_full_name") in unique_constraints


def test_repository_progress_and_version_have_database_checks() -> None:
    table = Base.metadata.tables["repositories"]
    checks = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert "ck_repositories_index_version_nonnegative" in checks
    assert "ck_repositories_indexing_progress_range" in checks


def test_indexing_processing_counts_have_database_checks() -> None:
    table = Base.metadata.tables["indexing_jobs"]
    checks = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert {
        "ck_indexing_jobs_parsed_file_count_nonnegative",
        "ck_indexing_jobs_partial_file_count_nonnegative",
        "ck_indexing_jobs_parser_skipped_file_count_nonnegative",
        "ck_indexing_jobs_symbol_count_nonnegative",
        "ck_indexing_jobs_chunk_count_nonnegative",
    } <= checks


def test_call_edges_have_composite_symbol_scope_foreign_keys() -> None:
    table = cast(Table, CallEdge.__table__)
    foreign_keys = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    assert "fk_call_edges_caller_symbol_scope" in foreign_keys
    assert "fk_call_edges_callee_symbol_scope" in foreign_keys


def test_tenant_and_version_query_indexes_exist() -> None:
    symbol_table = Base.metadata.tables["symbol_definitions"]
    call_table = Base.metadata.tables["call_edges"]
    repository_table = Base.metadata.tables["repositories"]
    symbol_indexes = {index.name for index in symbol_table.indexes}
    call_indexes = {index.name for index in call_table.indexes}
    assert "ix_symbol_definitions_repository_version_file" in symbol_indexes
    assert "ix_symbol_definitions_repository_version_name" in symbol_indexes
    assert "ix_call_edges_repository_version_caller" in call_indexes
    assert "ix_call_edges_repository_version_callee" in call_indexes
    assert all(isinstance(index, Index) for index in repository_table.indexes)
