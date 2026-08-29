import enum
import uuid

from sqlalchemy import Column, DateTime, Enum as SAEnum, ForeignKey, Integer, Numeric
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class RedemptionChoice(str, enum.Enum):
    CASH = "cash"
    TICKET = "ticket"


class PayoutStatus(str, enum.Enum):
    PENDING = "pending"  # cash redemption, organizer hasn't paid it out yet
    PAID = "paid"


class RewardRedemption(Base):
    """
    One instance of a referrer actually claiming a reward at a
    redemption tier — this is what points_spent is computed from (see
    app/services/redemptions.py's points_available), and doubles as the
    audit trail. cash_value / ticket_value / points_spent are snapshotted
    at redemption time from the tier and option that were current then,
    so a later change to either doesn't rewrite history.

    A TICKET choice is fulfilled immediately — created_guest_id links to
    the real Guest record created for it, same seating-resolution
    machinery used everywhere else in the app. payout_status is null for
    a ticket choice (nothing to pay out) and starts PENDING for a cash
    choice, since the app can't actually send money — it can only record
    that money is owed until the organizer marks it paid.
    """

    __tablename__ = "reward_redemptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    promo_code_id = Column(UUID(as_uuid=True), ForeignKey("promo_codes.id"), nullable=False)
    redemption_tier_id = Column(UUID(as_uuid=True), ForeignKey("redemption_tiers.id"), nullable=False)
    choice = Column(
        SAEnum(RedemptionChoice, name="redemption_choice", values_callable=lambda e: [x.value for x in e]),
        nullable=False,
    )
    points_spent = Column(Integer, nullable=False)
    cash_value = Column(Numeric, nullable=True)
    ticket_value = Column(Integer, nullable=True)
    created_guest_id = Column(UUID(as_uuid=True), ForeignKey("guests.id"), nullable=True)
    payout_status = Column(
        SAEnum(PayoutStatus, name="payout_status", values_callable=lambda e: [x.value for x in e]),
        nullable=True,
    )
    redeemed_at = Column(DateTime(timezone=True), server_default=func.now())