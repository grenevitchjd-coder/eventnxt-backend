import uuid

from sqlalchemy import Column, String, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class GuestType(Base):
    """
    Event-scoped (Celebrity, Sponsor, Volunteer, Model, etc.) — different
    events for the same org can define their own guest types, since who
    an org invites varies event to event. Its seating preferences live in
    GuestTypeSeatingPriority (an ordered list, not a single default) —
    see that model for why.
    event_id is a stored reference to Events360's Event, not a real
    foreign key (separate databases).
    """

    __tablename__ = "guest_types"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    name = Column(String, nullable=False)  # e.g. "Celebrity", "Sponsor", "Volunteer", "Model"
    created_at = Column(DateTime(timezone=True), server_default=func.now())