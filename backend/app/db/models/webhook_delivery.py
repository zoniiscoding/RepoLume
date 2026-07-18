"""Content-free GitHub delivery idempotency and freshness state."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.models.enums import WebhookDeliveryStatus, database_enum


class WebhookDelivery(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A content-free record of a GitHub webhook delivery identifier."""

    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        CheckConstraint("retry_count >= 0", name="retry_count_nonnegative"),
        Index("ix_webhook_deliveries_status_created", "status", "created_at"),
        Index(
            "ix_webhook_deliveries_repository_received",
            "repository_id",
            "received_at",
        ),
    )

    delivery_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    event_name: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str | None] = mapped_column(String(64))
    github_installation_id: Mapped[int | None] = mapped_column(BigInteger)
    github_repository_id: Mapped[int | None] = mapped_column(BigInteger)
    repository_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("repositories.id", ondelete="SET NULL")
    )
    indexing_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("indexing_jobs.id", ondelete="SET NULL")
    )
    ref: Mapped[str | None] = mapped_column(String(512))
    before_commit_sha: Mapped[str | None] = mapped_column(String(64))
    after_commit_sha: Mapped[str | None] = mapped_column(String(64))
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    status: Mapped[WebhookDeliveryStatus] = mapped_column(
        database_enum(WebhookDeliveryStatus, name="webhook_delivery_status"),
        nullable=False,
        default=WebhookDeliveryStatus.RECEIVED,
        server_default=WebhookDeliveryStatus.RECEIVED.value,
    )
    safe_error_code: Mapped[str | None] = mapped_column(String(64))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
