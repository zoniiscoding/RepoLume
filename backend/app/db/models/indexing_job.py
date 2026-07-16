"""Durable indexing and deletion job state."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.models.enums import IndexingJobStatus, IndexingJobType, database_enum


class IndexingJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """PostgreSQL source of truth for a repository background operation."""

    __tablename__ = "indexing_jobs"
    __table_args__ = (
        CheckConstraint("attempt >= 0", name="attempt_nonnegative"),
        CheckConstraint("progress >= 0 AND progress <= 100", name="progress_range"),
        Index("ix_indexing_jobs_repository_status", "repository_id", "status", "created_at"),
        Index("ix_indexing_jobs_requester_created", "requested_by_user_id", "created_at"),
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
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
