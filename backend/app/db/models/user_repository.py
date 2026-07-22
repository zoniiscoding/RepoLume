"""Per-user attachment to a safely authorized repository record."""

import uuid

from sqlalchemy import ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class UserRepository(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Authorize one canonical user to see one repository."""

    __tablename__ = "user_repositories"
    __table_args__ = (
        UniqueConstraint("user_id", "repository_id", name="uq_user_repositories_membership"),
        Index("ix_user_repositories_repository", "repository_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
