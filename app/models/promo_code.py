import enum
import uuid

from sqlalchemy import Column, DateTime, Enum as SAEnum, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class RewardType(str, enum.Enum):
    FLAT_AMOUNT = "flat_amount"  # reward_value is a dollar amount per sale
    PERCENTAGE = "percentage"  # reward_value is a percent of the sale amount (needs Sale.amount to compute)
    FREE_TICKETS = "free_tickets"  # reward_value is a ticket count owed, not a dollar figure


class PromoCode(Base):
    """
    A referral/promo code belonging to one referrer (a Guest — a
    referrer is just a person in the system, same as anyone else, so
    this reuses Guest rather than inventing a parallel "contact" concept).
    One referrer can hold several codes (e.g. one per channel), each with
    its own reward terms, so two codes for the same person aren't forced
    to share a deal.

    referral_message_draft stores the "here's what to send your friends"
    text an organizer drafts for the referrer — captured even though
    there's no automated email to send it yet, so it's not lost once
    that exists.

    event_id is a stored reference to Events360's Event, not a real
    foreign key. guest_id IS a real foreign key — Guest lives in this
    same database.
    """

    __tablename__ = "promo_codes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    guest_id = Column(UUID(as_uuid=True), ForeignKey("guests.id"), nullable=False)
    code = Column(String, nullable=False, index=True)  # unique per event, enforced at the DB level
    reward_type = Column(
        SAEnum(RewardType, name="reward_type", values_callable=lambda e: [x.value for x in e]), nullable=False
    )
    reward_value = Column(Numeric, nullable=False)
    referral_message_draft = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())