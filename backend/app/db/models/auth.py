"""OAuth state and RepoLume refresh-token persistence."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.models.enums import AuthProvider, OAuthFlow, database_enum


class AuthIdentity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One immutable external provider subject attached to a canonical user."""

    __tablename__ = "auth_identities"
    __table_args__ = (
        UniqueConstraint("provider", "provider_subject", name="uq_auth_identities_subject"),
        Index("ix_auth_identities_user", "user_id"),
        Index("ix_auth_identities_verified_email", "provider_email", "email_verified"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[AuthProvider] = mapped_column(
        database_enum(AuthProvider, name="auth_provider"), nullable=False
    )
    provider_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_email: Mapped[str | None] = mapped_column(String(320))
    email_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )


class OAuthState(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One-time, provider-bound OAuth state, PKCE, nonce, and linking intent."""

    __tablename__ = "oauth_states"
    __table_args__ = (Index("ix_oauth_states_expiry_use", "expires_at", "used_at"),)

    state_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    code_verifier_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[AuthProvider] = mapped_column(
        database_enum(AuthProvider, name="oauth_provider"),
        nullable=False,
        default=AuthProvider.GITHUB,
        server_default=AuthProvider.GITHUB.value,
    )
    flow: Mapped[OAuthFlow] = mapped_column(
        database_enum(OAuthFlow, name="oauth_flow"),
        nullable=False,
        default=OAuthFlow.SIGN_IN,
        server_default=OAuthFlow.SIGN_IN.value,
    )
    nonce_hash: Mapped[str | None] = mapped_column(String(64))
    intended_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RefreshToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A hashed opaque refresh token within a revocable rotation family."""

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("ix_refresh_tokens_family_active", "family_id", "revoked_at", "expires_at"),
        Index("ix_refresh_tokens_user_active", "user_id", "revoked_at", "expires_at"),
    )

    family_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    parent_token_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("refresh_tokens.id", ondelete="SET NULL"),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocation_reason: Mapped[str | None] = mapped_column(String(64))
