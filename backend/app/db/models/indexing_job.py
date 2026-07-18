"""Durable indexing and deletion job state."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.models.enums import IndexingJobStatus, IndexingJobType, IndexingMode, database_enum


class IndexingJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """PostgreSQL source of truth for a repository background operation."""

    __tablename__ = "indexing_jobs"
    __table_args__ = (
        CheckConstraint("attempt >= 0", name="attempt_nonnegative"),
        CheckConstraint("progress >= 0 AND progress <= 100", name="progress_range"),
        CheckConstraint("discovered_file_count >= 0", name="discovered_file_count_nonnegative"),
        CheckConstraint("discovered_total_bytes >= 0", name="discovered_total_bytes_nonnegative"),
        CheckConstraint("parsed_file_count >= 0", name="parsed_file_count_nonnegative"),
        CheckConstraint("partial_file_count >= 0", name="partial_file_count_nonnegative"),
        CheckConstraint(
            "parser_skipped_file_count >= 0",
            name="parser_skipped_file_count_nonnegative",
        ),
        CheckConstraint("symbol_count >= 0", name="symbol_count_nonnegative"),
        CheckConstraint("chunk_count >= 0", name="chunk_count_nonnegative"),
        CheckConstraint("embedded_chunk_count >= 0", name="embedded_chunk_count_nonnegative"),
        CheckConstraint("vector_count >= 0", name="vector_count_nonnegative"),
        CheckConstraint("embedding_failed_count >= 0", name="embedding_failed_count_nonnegative"),
        CheckConstraint("embedding_skipped_count >= 0", name="embedding_skipped_count_nonnegative"),
        CheckConstraint("call_site_count >= 0", name="call_site_count_nonnegative"),
        CheckConstraint("exact_edge_count >= 0", name="exact_edge_count_nonnegative"),
        CheckConstraint("ambiguous_edge_count >= 0", name="ambiguous_edge_count_nonnegative"),
        CheckConstraint("unresolved_call_count >= 0", name="unresolved_call_count_nonnegative"),
        CheckConstraint("graph_warning_count >= 0", name="graph_warning_count_nonnegative"),
        CheckConstraint(
            "target_index_version IS NULL OR target_index_version >= 1",
            name="target_index_version_positive",
        ),
        CheckConstraint(
            "embedding_dimension IS NULL OR embedding_dimension > 0",
            name="embedding_dimension_positive",
        ),
        CheckConstraint("refresh_generation >= 0", name="refresh_generation_nonnegative"),
        CheckConstraint("changed_file_count >= 0", name="changed_file_count_nonnegative"),
        CheckConstraint("reused_chunk_count >= 0", name="reused_chunk_count_nonnegative"),
        CheckConstraint("reembedded_chunk_count >= 0", name="reembedded_chunk_count_nonnegative"),
        Index("ix_indexing_jobs_repository_status", "repository_id", "status", "created_at"),
        Index("ix_indexing_jobs_requester_created", "requested_by_user_id", "created_at"),
        Index("ix_indexing_jobs_status_next_attempt", "status", "next_attempt_at"),
        Index(
            "uq_indexing_jobs_repository_active",
            "repository_id",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
    )

    repository_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
    )
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    job_type: Mapped[IndexingJobType] = mapped_column(
        database_enum(IndexingJobType, name="indexing_job_type"),
        nullable=False,
    )
    status: Mapped[IndexingJobStatus] = mapped_column(
        database_enum(IndexingJobStatus, name="indexing_job_status"),
        nullable=False,
        default=IndexingJobStatus.QUEUED,
        server_default=IndexingJobStatus.QUEUED.value,
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    stage: Mapped[str | None] = mapped_column(String(64))
    source_commit_sha: Mapped[str | None] = mapped_column(String(64))
    target_commit_sha: Mapped[str | None] = mapped_column(String(64))
    target_branch: Mapped[str | None] = mapped_column(String(255))
    requested_mode: Mapped[IndexingMode | None] = mapped_column(
        database_enum(IndexingMode, name="requested_indexing_mode")
    )
    actual_mode: Mapped[IndexingMode | None] = mapped_column(
        database_enum(IndexingMode, name="actual_indexing_mode")
    )
    full_rebuild_reason: Mapped[str | None] = mapped_column(String(64))
    refresh_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    changed_file_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    changed_files_json: Mapped[dict[str, int]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    reused_chunk_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    reembedded_chunk_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    graph_rebuilt: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    safe_error_message: Mapped[str | None] = mapped_column(String(512))
    locked_by: Mapped[str | None] = mapped_column(String(128))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discovered_file_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    discovered_total_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    skipped_files_json: Mapped[dict[str, int]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    parsed_file_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    partial_file_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    parser_skipped_file_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    symbol_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    target_index_version: Mapped[int | None] = mapped_column(Integer)
    embedded_chunk_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    vector_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    embedding_failed_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    embedding_skipped_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    embedding_model_identifier: Mapped[str | None] = mapped_column(String(255))
    embedding_model_revision: Mapped[str | None] = mapped_column(String(64))
    embedding_dimension: Mapped[int | None] = mapped_column(Integer)
    preprocessing_fingerprint: Mapped[str | None] = mapped_column(String(64))
    parser_warnings_json: Mapped[dict[str, int]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    call_site_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    exact_edge_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    ambiguous_edge_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    unresolved_call_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    graph_warning_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
