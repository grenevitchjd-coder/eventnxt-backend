# eventnxt-backend: app/models/payment_account.py
import uuid

from sqlalchemy import Boolean, Column, DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class PaymentAccount(Base):
    """
    An organization's connected Stripe (Express) account — where ticket
    money goes. ONE row per Events360 org: connect once, every event the
    org runs pays out through it.

    This table deliberately stores NOTHING sensitive: the acct_... id and
    three status booleans mirrored from Stripe. Bank accounts, tax ids,
    and identity documents live with Stripe, entered on Stripe-hosted
    onboarding — EventNXT never sees them.

    The three booleans are Stripe's, kept fresh by the account.updated
    Connect webhook (never computed locally):
      details_submitted — they finished the onboarding form.
      charges_enabled  — Stripe will accept payments destined for them.
      payouts_enabled  — Stripe will pay their bank out.
    charges_enabled is the one that gates selling (slice 2).
    """

    __tablename__ = "payment_accounts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), nullable=False, unique=True, index=True)
    stripe_account_id = Column(String, nullable=False, unique=True, index=True)
    charges_enabled = Column(Boolean, nullable=False, default=False)
    payouts_enabled = Column(Boolean, nullable=False, default=False)
    details_submitted = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=True, onupdate=func.now())