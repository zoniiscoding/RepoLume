"""Versioned best-effort static call relationships."""

import uuid

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin
from app.db.models.enums import Confidence, ResolutionType, database_enum


class CallEdge(UUIDPrimaryKeyMixin, Base):
    """A statically observed call, explicitly scoped to an index version."""

    __tablename__ = "call_edges"
    __table_args__ = (
        ForeignKeyConstraint(
            ["caller_symbol_id", "repository_id", "index_version"],
            [
                "symbol_definitions.id",
                "symbol_definitions.repository_id",
                "symbol_definitions.index_version",
            ],
            name="fk_call_edges_caller_symbol_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["callee_symbol_id", "repository_id", "index_version"],
            [
                "symbol_definitions.id",
                "symbol_definitions.repository_id",
                "symbol_definitions.index_version",
            ],
            name="fk_call_edges_callee_symbol_scope",
            ondelete="CASCADE",
        ),
        CheckConstraint("index_version >= 1", name="index_version_positive"),
        CheckConstraint("call_line >= 1", name="call_line_positive"),
        CheckConstraint("call_end_line >= call_line", name="call_line_range_ordered"),
        CheckConstraint(
            "callee_symbol_id IS NOT NULL OR unresolved_callee_name IS NOT NULL",
            name="callee_or_unresolved_required",
        ),
        Index(
            "ix_call_edges_repository_version_callee",
            "repository_id",
            "index_version",
            "callee_symbol_id",
        ),
        UniqueConstraint(
            "repository_id",
            "index_version",
            "call_site_fingerprint",
            name="uq_call_edges_repository_version_site",
        ),
        Index(
            "ix_call_edges_repository_version_caller",
            "repository_id",
            "index_version",
            "caller_symbol_id",
        ),
    )

    repository_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
    )
    index_version: Mapped[int] = mapped_column(Integer, nullable=False)
    caller_symbol_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    callee_symbol_id: Mapped[uuid.UUID | None] = mapped_column()
    unresolved_callee_name: Mapped[str | None] = mapped_column(String(1024))
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    call_line: Mapped[int] = mapped_column(Integer, nullable=False)
    call_end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    call_expression: Mapped[str] = mapped_column(String(2048), nullable=False)
    call_site_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    resolution_type: Mapped[ResolutionType] = mapped_column(
        database_enum(ResolutionType, name="call_resolution_type"),
        nullable=False,
    )
    confidence: Mapped[Confidence] = mapped_column(
        database_enum(Confidence, name="call_confidence"),
        nullable=False,
    )
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
