"""Repository-scoped chat persistence."""

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.models.enums import AnswerStatus, ChatRole, Confidence, database_enum


class ChatSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A chat session scoped to exactly one repository."""

    __tablename__ = "chat_sessions"
    __table_args__ = (
        Index("ix_chat_sessions_repository_updated", "repository_id", "updated_at"),
        Index("ix_chat_sessions_creator_updated", "created_by_user_id", "updated_at"),
    )

    repository_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)


class ChatMessage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A user, assistant, or system event in a repository-scoped session."""

    __tablename__ = "chat_messages"
    __table_args__ = (
        CheckConstraint("char_length(content) > 0", name="content_nonempty"),
        Index("ix_chat_messages_session_created", "session_id", "created_at"),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[ChatRole] = mapped_column(
        database_enum(ChatRole, name="chat_role"),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    answer_status: Mapped[AnswerStatus | None] = mapped_column(
        database_enum(AnswerStatus, name="answer_status")
    )
    confidence: Mapped[Confidence | None] = mapped_column(
        database_enum(Confidence, name="answer_confidence")
    )
    tool_trace_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    evidence_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    indexed_commit_sha: Mapped[str | None] = mapped_column(String(64))
