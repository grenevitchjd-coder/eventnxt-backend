import uuid

from sqlalchemy import Column, DateTime, Integer, Numeric
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class EventBonusTier(Base):
    """
    The organizer's DEFAULT volume-bonus structure for the event — "sell
    20 tickets, get a $50 bonus" as the standard deal every code starts
    with. A specific code can override this entirely (see
    PromoCode.bonus_tiers_overridden / PromoCodeBonusTier) if that
    referrer's deal is different, matching the same default+override
    pattern already used for ticket allotments.

    bonus_value is denominated in whatever unit the code's OWN
    reward_type already uses — dollars for a flat_amount or percentage
    code, points for a points code, a ticket count for a free_tickets
    code. There's no separate "bonus type" field: a bonus is just more
    of the same currency the code already earns, so it can never end up
    mismatched or need its own conversion.
    """

    __tablename__ = "event_bonus_tiers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    tickets_required = Column(Integer, nullable=False)  # cumulative sales attributed to a code
    bonus_value = Column(Numeric, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())