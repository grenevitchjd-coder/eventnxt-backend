import uuid

from sqlalchemy import Column, String, Integer, DateTime, Time, Boolean, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class EventProfileScheduleItem(Base):
    """
    One line of an optional, fully custom public schedule. Two modes:
    - One-time: a specific date + time ("VIP Preview" on Sept 9, 5pm).
      Uses `event_datetime`.
    - Daily: just a time of day ("Doors Open" at 6:00 PM), applied
      automatically to every day of the event's real date range — set
      once, no need to re-enter the same pattern per night. Uses
      `time_of_day`; expanded into concrete per-day instances only when
      the PUBLIC page renders the schedule (the organizer-facing editor
      shows the raw pattern as one row, so editing/deleting it acts on
      the whole pattern, not one expanded instance).
    Entirely optional — zero items is fine, the cached event dates alone
    cover the simple case.
    """

    __tablename__ = "event_profile_schedule_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_profile_id = Column(UUID(as_uuid=True), ForeignKey("event_profiles.id"), nullable=False)
    label = Column(String, nullable=False)  # e.g. "Doors Open", "VIP Preview"
    is_recurring = Column(Boolean, nullable=False, default=False)
    event_datetime = Column(DateTime(timezone=True), nullable=True)  # one-time mode
    time_of_day = Column(Time, nullable=True)  # daily mode
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())