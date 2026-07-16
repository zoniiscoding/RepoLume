"""GitHub App installation and user membership records."""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.models.enums import (
    GitHubAccountType,
    InstallationMemberRole,
    InstallationStatus,
    RepositorySelection,
    database_enum,
)


class GitHubInstallation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A GitHub App installation known to RepoLume."""

    __tablename__ = "github_installations"
    __table_args__ = (Index("ix_github_installations_account", "account_github_id", "status"),)

    github_installation_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    account_type: Mapped[GitHubAccountType] = mapped_column(
        database_enum(GitHubAccountType, name="github_account_type"),
        nullable=False,
    )
    account_github_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    account_login: Mapped[str] = mapped_column(String(255), nullable=False)
    installed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    status: Mapped[InstallationStatus] = mapped_column(
        database_enum(InstallationStatus, name="installation_status"),
        nullable=False,
        default=InstallationStatus.ACTIVE,
        server_default=InstallationStatus.ACTIVE.value,
    )
    permissions_json: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    repository_selection: Mapped[RepositorySelection] = mapped_column(
        database_enum(RepositorySelection, name="repository_selection"),
        nullable=False,
    )
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InstallationMember(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A user authorized to act through an installation."""

    __tablename__ = "installation_members"
    __table_args__ = (
        UniqueConstraint(
            "installation_id",
            "user_id",
            name="uq_installation_members_installation_user",
        ),
        Index("ix_installation_members_user_installation", "user_id", "installation_id"),
    )

    installation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("github_installations.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[InstallationMemberRole] = mapped_column(
        database_enum(InstallationMemberRole, name="installation_member_role"),
        nullable=False,
    )
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
