"""eventnxt-backend: app/models/order.py"""

import enum
import uuid

from sqlalchemy import Column, DateTime, Enum as SAEnum, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class OrderStatus(str, enum.Enum):
    PENDING = "pending"  # created, holding inventory, waiting on Stripe (or instant-paid if total is 0)
    PAID = "paid"  # the webhook confirmed payment — the ONLY path to paid for nonzero totals
    EXPIRED = "expired"  # hold aged out unpaid; kept for record, holds nothing
    REFUNDED = "refunded"  # full-order refund issued (v1 has no partial refunds)


class Order(Base):
    """
    One purchase attempt, from checkout click to (hopefully) paid.

    Inventory semantics: a PENDING order holds its tickets until
    expires_at (matched to the Stripe Checkout session's expiry).
    Availability math counts paid orders plus unexpired pending ones —
    an expired pending order stops counting by pure passage of time, so
    abandoned carts self-release with no cleanup job. Status EXPIRED is
    set lazily when convenient; the timestamp, not the status, is what
    frees the hold.

    Money snapshots: subtotal_cents (face value the buyer paid),
    platform_fee_cents (EventNXT's fee at TODAY's rate), and
    organizer_net_cents (subtotal minus platform fee) are frozen at
    creation — repricing the platform fee later never rewrites history
    (same snapshot rule as redemptions and bonus awards).
    organization_id is snapshotted too so the per-organizer ledger and a
    future Stripe Connect cutover need nothing recomputed.

    order_token is the buyer's self-serve key — same pattern as
    rsvp_token: possession of the link IS the authentication, no login.

    The webhook is the source of truth for payment, never the redirect:
    stripe_checkout_session_id is how the checkout.session.completed
    event finds this order.
    """

    __tablename__ = "orders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), nullable=False, index=True)  # Events360 event, stored ref not FK
    organization_id = Column(UUID(as_uuid=True), nullable=False, index=True)  # snapshot for the organizer ledger

    status = Column(SAEnum(OrderStatus), nullable=False, default=OrderStatus.PENDING)

    buyer_name = Column(String, nullable=False)
    buyer_email = Column(String, nullable=False, index=True)  # lowercased at write time; "find my tickets" key

    currency = Column(String, nullable=False, default="usd")
    subtotal_cents = Column(Integer, nullable=False)
    platform_fee_cents = Column(Integer, nullable=False)
    organizer_net_cents = Column(Integer, nullable=False)

    # Phase 2 hook — promo code applied at checkout (validated before the
    # Stripe redirect, so attribution is known at purchase, not matched
    # after the fact like CSV imports).
    promo_code_id = Column(UUID(as_uuid=True), ForeignKey("promo_codes.id"), nullable=True)

    stripe_checkout_session_id = Column(String, nullable=True, unique=True, index=True)
    stripe_payment_intent_id = Column(String, nullable=True)

    order_token = Column(String, nullable=False, unique=True, index=True)

    expires_at = Column(DateTime(timezone=True), nullable=True)  # pending-hold deadline; null once paid
    paid_at = Column(DateTime(timezone=True), nullable=True)
    refunded_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())