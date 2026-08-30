"""eventnxt-backend: app/models/stripe_webhook_event.py"""

import uuid

from sqlalchemy import Column, DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class StripeWebhookEvent(Base):
    """
    Idempotency ledger for Stripe webhooks. Stripe WILL deliver the same
    event more than once (retries, network hiccups) — that's documented
    behavior, not an edge case. Before processing any event, the handler
    inserts its stripe_event_id here; the unique constraint makes the
    second delivery of the same event a clean no-op instead of a
    double-fulfilled order.
    """

    __tablename__ = "stripe_webhook_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    stripe_event_id = Column(String, nullable=False, unique=True, index=True)
    event_type = Column(String, nullable=False)
    received_at = Column(DateTime(timezone=True), server_default=func.now())