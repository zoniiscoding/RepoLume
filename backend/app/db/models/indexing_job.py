"""Durable indexing and deletion job state."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
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
from app.db.models.enums import IndexingJobStatus, IndexingJobType, database_enum


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
        Index("ix_indexing_jobs_repository_status", "repository_id", "status", "created_at"),
        Index("ix_indexing_jobs_requester_created", "requested_by_user_id", "created_at"),
        Index("ix_indexing_jobs_status_next_attempt", "status", "next_attempt_at"),
        Index(
            "uq_indexing_jobs_repository_active",
            "repository_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running', 'retrying')"),
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
    parser_warnings_json: Mapped[dict[str, int]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
