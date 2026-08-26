import uuid

from sqlalchemy import Column, String, Integer, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class EventProfileScheduleItem(Base):
    """
    One line of an optional, fully custom public schedule — "Doors Open"
    at a specific date+time, "Show Starts" at another, etc. Also how a
    multi-day event with different times per day gets represented: one
    item per day/moment, rather than a single date range. Entirely
    optional — zero items is fine, the cached event dates alone cover the
    simple case.
    """

    __tablename__ = "event_profile_schedule_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_profile_id = Column(UUID(as_uuid=True), ForeignKey("event_profiles.id"), nullable=False)
    label = Column(String, nullable=False)  # e.g. "Doors Open", "Day 2 — Show Starts"
    event_datetime = Column(DateTime(timezone=True), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())