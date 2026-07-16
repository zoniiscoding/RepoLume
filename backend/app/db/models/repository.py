"""Authorized GitHub repository indexing state."""

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
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.models.enums import RepositoryIndexingStatus, database_enum


class Repository(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """RepoLume state for one repository selected through an installation."""

    __tablename__ = "repositories"
    __table_args__ = (
        UniqueConstraint(
            "installation_id",
            "github_repository_id",
            name="uq_repositories_installation_github_repository",
        ),
        UniqueConstraint(
            "installation_id",
            "github_full_name",
            name="uq_repositories_installation_full_name",
        ),
        CheckConstraint("index_version >= 0", name="index_version_nonnegative"),
        CheckConstraint(
            "indexing_progress >= 0 AND indexing_progress <= 100",
            name="indexing_progress_range",
        ),
        CheckConstraint("size_bytes IS NULL OR size_bytes >= 0", name="size_bytes_nonnegative"),
        CheckConstraint("active_vector_count >= 0", name="active_vector_count_nonnegative"),
        Index(
            "ix_repositories_installation_status",
            "installation_id",
            "indexing_status",
            "deleted_at",
        ),
        Index("ix_repositories_github_full_name", "github_full_name"),
    )

    installation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("github_installations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    github_repository_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    github_owner: Mapped[str] = mapped_column(String(255), nullable=False)
    github_name: Mapped[str] = mapped_column(String(255), nullable=False)
    github_full_name: Mapped[str] = mapped_column(String(512), nullable=False)
    github_url: Mapped[str] = mapped_column(Text, nullable=False)
    is_private: Mapped[bool] = mapped_column(Boolean, nullable=False)
    default_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    current_remote_sha: Mapped[str | None] = mapped_column(String(64))
    last_indexed_commit_sha: Mapped[str | None] = mapped_column(String(64))
    index_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    indexing_status: Mapped[RepositoryIndexingStatus] = mapped_column(
        database_enum(RepositoryIndexingStatus, name="repository_indexing_status"),
        nullable=False,
        default=RepositoryIndexingStatus.NOT_INDEXED,
        server_default=RepositoryIndexingStatus.NOT_INDEXED.value,
    )
    indexing_progress: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    indexing_stage: Mapped[str | None] = mapped_column(String(64))
    indexing_error_code: Mapped[str | None] = mapped_column(String(64))
    indexing_error_message: Mapped[str | None] = mapped_column(String(512))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    primary_language: Mapped[str | None] = mapped_column(String(64))
    last_indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active_vector_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    access_revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
