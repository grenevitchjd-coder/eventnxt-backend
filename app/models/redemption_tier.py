import uuid

from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class RedemptionTier(Base):
    """
    One point threshold in the event's shared redemption menu — "at 50
    points" is one tier, "at 100 points" is another. The THRESHOLD
    structure is the same for every referrer at this event (confirmed
    explicitly); what a specific referrer's code actually offers at each
    threshold is per-code — see PromoCodeRedemptionOption. A code with no
    option row for a given tier simply doesn't participate in that tier.

    No update endpoint on purpose — points_required shouldn't change
    after referrers may have already redeemed against it, since that
    would retroactively change what "eligible" meant for a real past
    redemption. To restructure, delete (blocked once any redemption has
    used it) and recreate.
    """

    __tablename__ = "redemption_tiers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    points_required = Column(Integer, nullable=False)
    label = Column(String, nullable=True)  # optional organizer-facing name, e.g. "Bronze"
    created_at = Column(DateTime(timezone=True), server_default=func.now())