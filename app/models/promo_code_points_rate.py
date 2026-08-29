import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class PromoCodePointsRate(Base):
    """
    One ticket-type's points-earning rate for a POINTS-type promo code —
    "VIP sale earns 10 points, GA sale earns 5" is two rows. Fully
    per-code by design (confirmed explicitly): different referrers can
    have entirely different rates for the same ticket type, there's no
    event-wide default this falls back to. ticket_type is matched
    case-insensitively against Sale.ticket_type at reconciliation time;
    a sale whose ticket type has no matching row here simply earns 0
    points for that sale, not an error.
    """

    __tablename__ = "promo_code_points_rates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    promo_code_id = Column(UUID(as_uuid=True), ForeignKey("promo_codes.id"), nullable=False)
    ticket_type = Column(String, nullable=False)
    points = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())