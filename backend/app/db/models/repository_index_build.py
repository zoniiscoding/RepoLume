"""Durable PostgreSQL truth for inactive and active searchable index versions."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.models.enums import IndexBuildState, IndexCleanupStatus, database_enum


class RepositoryIndexBuild(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One immutable repository/version identity with mutable build lifecycle state."""

    __tablename__ = "repository_index_builds"
    __table_args__ = (
        UniqueConstraint(
            "repository_id",
            "index_version",
            name="uq_repository_index_builds_repository_version",
        ),
        UniqueConstraint("job_id", name="uq_repository_index_builds_job"),
        CheckConstraint("index_version >= 1", name="index_version_positive"),
        CheckConstraint("embedding_dimension > 0", name="embedding_dimension_positive"),
        CheckConstraint("expected_chunk_count >= 0", name="expected_chunk_count_nonnegative"),
        CheckConstraint("embedded_chunk_count >= 0", name="embedded_chunk_count_nonnegative"),
        CheckConstraint("vector_count >= 0", name="vector_count_nonnegative"),
        CheckConstraint("failed_chunk_count >= 0", name="failed_chunk_count_nonnegative"),
        CheckConstraint("skipped_chunk_count >= 0", name="skipped_chunk_count_nonnegative"),
        CheckConstraint(
            "state NOT IN ('ready', 'active') OR "
            "(expected_chunk_count = embedded_chunk_count AND "
            "expected_chunk_count = vector_count AND failed_chunk_count = 0 AND "
            "skipped_chunk_count = 0)",
            name="ready_counts_match",
        ),
        CheckConstraint(
            "state != 'active' OR cleanup_status = 'not_required'",
            name="active_cleanup_not_required",
        ),
        Index("ix_repository_index_builds_repository_state", "repository_id", "state"),
        Index("ix_repository_index_builds_cleanup", "cleanup_status", "updated_at"),
        Index(
            "uq_repository_index_builds_repository_active",
            "repository_id",
            unique=True,
            postgresql_where=text("state = 'active'"),
        ),
    )

    repository_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("indexing_jobs.id", ondelete="SET NULL")
    )
    index_version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[IndexBuildState] = mapped_column(
        database_enum(IndexBuildState, name="index_build_state"),
        nullable=False,
        default=IndexBuildState.BUILDING,
        server_default=IndexBuildState.BUILDING.value,
    )
    cleanup_status: Mapped[IndexCleanupStatus] = mapped_column(
        database_enum(IndexCleanupStatus, name="index_cleanup_status"),
        nullable=False,
        default=IndexCleanupStatus.NOT_REQUIRED,
        server_default=IndexCleanupStatus.NOT_REQUIRED.value,
    )
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_model_identifier: Mapped[str] = mapped_column(String(255), nullable=False)
    embedding_model_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    preprocessing_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_chunk_count: Mapped[int] = mapped_column(Integer, nullable=False)
    embedded_chunk_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    vector_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    failed_chunk_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    skipped_chunk_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cleanup_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
