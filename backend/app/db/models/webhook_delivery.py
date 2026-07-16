"""Minimal durable GitHub delivery idempotency state for Milestone 2."""

from datetime import datetime

from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.models.enums import WebhookDeliveryStatus, database_enum


class WebhookDelivery(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A content-free record of a GitHub webhook delivery identifier."""

    __tablename__ = "webhook_deliveries"
    __table_args__ = (Index("ix_webhook_deliveries_status_created", "status", "created_at"),)

    delivery_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    event_name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[WebhookDeliveryStatus] = mapped_column(
        database_enum(WebhookDeliveryStatus, name="webhook_delivery_status"),
        nullable=False,
        default=WebhookDeliveryStatus.RECEIVED,
        server_default=WebhookDeliveryStatus.RECEIVED.value,
    )
    safe_error_code: Mapped[str | None] = mapped_column(String(64))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
