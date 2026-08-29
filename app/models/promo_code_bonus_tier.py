import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class PromoCodeBonusTier(Base):
    """
    One code's own override of the event's default bonus structure —
    same shape as EventBonusTier, but only used when
    PromoCode.bonus_tiers_overridden is True. Same override mechanism as
    ticket allotments: presence of the flag (not presence of rows) is
    what signals "this code has its own deal" — a code can be overridden
    to an empty set of tiers, meaning "no bonuses at all for this one,"
    distinct from "not overridden, inherits the event default."
    """

    __tablename__ = "promo_code_bonus_tiers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    promo_code_id = Column(UUID(as_uuid=True), ForeignKey("promo_codes.id"), nullable=False)
    tickets_required = Column(Integer, nullable=False)
    bonus_value = Column(Numeric, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())