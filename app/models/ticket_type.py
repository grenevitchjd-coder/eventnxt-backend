"""eventnxt-backend: app/models/ticket_type.py"""

import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class TicketType(Base):
    """
    One sellable ticket kind for an event — "General Admission", "Front
    Row", "VIP Table for 8". This is the unit buyers purchase and the unit
    inventory is counted in.

    quantity is the sellable cap for THIS ticket type. Availability is
    always computed, never stored: quantity minus tickets on paid orders
    minus tickets on unexpired pending orders (a pending order HOLDS
    inventory until its expires_at passes — abandoned checkouts release
    themselves by simply aging out; no cleanup job).

    seating_category_id links the ticket type into the seating world —
    a real FK (same database, unlike the cross-service event_id) — so
    native sales, guest-list holds, and the Seating Summary all drain and
    reconcile against the same pool. Nullable: a ticket type doesn't have
    to map to seating (e.g. a livestream pass).

    price_cents: money is integer cents everywhere in ticketing, because
    Stripe speaks cents and floats lose pennies. (The reconciled Sale
    table predates this and uses Numeric dollars — the webhook converts
    when it writes Sale rows.)
    """

    __tablename__ = "ticket_types"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), nullable=False, index=True)  # Events360 event, stored ref not FK
    seating_category_id = Column(UUID(as_uuid=True), ForeignKey("seating_categories.id"), nullable=True)

    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    price_cents = Column(Integer, nullable=False)  # 0 is legal: a free/comp ticket type skips Stripe entirely
    currency = Column(String, nullable=False, default="usd")
    quantity = Column(Integer, nullable=False)
    max_per_order = Column(Integer, nullable=False, default=10)

    # Optional sales window — outside it the type shows as "not on sale".
    sales_start = Column(DateTime(timezone=True), nullable=True)
    sales_end = Column(DateTime(timezone=True), nullable=True)

    is_active = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())