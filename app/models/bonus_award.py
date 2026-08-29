import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class BonusAward(Base):
    """
    One instance of a code actually crossing a volume threshold and
    getting the bonus — created automatically when a sales import pushes
    a code's cumulative sale count past a configured tier, exactly once
    per tier per code. tickets_required / bonus_value are snapshotted
    here rather than referenced by a foreign key to whichever tier
    triggered it (event default or code override), so a later change to
    that tier's configuration never rewrites a bonus that's already been
    given. This table is also what stops the same tier from being
    awarded twice if sales get re-imported or recomputed — a tier
    already present here for a code is never re-checked.
    """

    __tablename__ = "bonus_awards"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    promo_code_id = Column(UUID(as_uuid=True), ForeignKey("promo_codes.id"), nullable=False)
    tickets_required = Column(Integer, nullable=False)
    bonus_value = Column(Numeric, nullable=False)
    awarded_at = Column(DateTime(timezone=True), server_default=func.now())