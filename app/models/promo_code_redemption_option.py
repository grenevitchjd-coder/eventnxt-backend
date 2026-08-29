import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class PromoCodeRedemptionOption(Base):
    """
    What ONE promo code's referrer can choose at ONE redemption tier —
    "at the 50-point tier, Benzo can pick $20 cash or 1 free ticket."
    Both cash_value and ticket_value may be set (offering a real choice
    at redemption time), or just one (that tier only offers cash, or
    only a ticket, for this particular code). Unique per
    (promo_code_id, redemption_tier_id) — one fill-in per code per tier.
    """

    __tablename__ = "promo_code_redemption_options"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    promo_code_id = Column(UUID(as_uuid=True), ForeignKey("promo_codes.id"), nullable=False)
    redemption_tier_id = Column(UUID(as_uuid=True), ForeignKey("redemption_tiers.id"), nullable=False)
    cash_value = Column(Numeric, nullable=True)
    ticket_value = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())