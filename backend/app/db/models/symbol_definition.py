"""Versioned static symbol definitions."""

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin
from app.db.models.enums import SymbolType, database_enum


class SymbolDefinition(UUIDPrimaryKeyMixin, Base):
    """A statically parsed symbol under one repository index version."""

    __tablename__ = "symbol_definitions"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "repository_id",
            "index_version",
            name="uq_symbol_definitions_identity_scope",
        ),
        UniqueConstraint(
            "repository_id",
            "index_version",
            "file_path",
            "qualified_name",
            "start_line",
            name="uq_symbol_definitions_location",
        ),
        CheckConstraint("index_version >= 1", name="index_version_positive"),
        CheckConstraint("start_line >= 1", name="start_line_positive"),
        CheckConstraint("end_line >= start_line", name="line_range_ordered"),
        Index(
            "ix_symbol_definitions_repository_version_file",
            "repository_id",
            "index_version",
            "file_path",
        ),
        Index(
            "ix_symbol_definitions_repository_version_name",
            "repository_id",
            "index_version",
            "symbol_name",
        ),
        Index("ix_symbol_definitions_content_hash", "content_hash"),
    )

    repository_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
    )
    index_version: Mapped[int] = mapped_column(Integer, nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    language: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol_name: Mapped[str] = mapped_column(String(512), nullable=False)
    qualified_name: Mapped[str] = mapped_column(String(1024), nullable=False)
    symbol_type: Mapped[SymbolType] = mapped_column(
        database_enum(SymbolType, name="symbol_type"),
        nullable=False,
    )
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
