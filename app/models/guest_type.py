import uuid

from sqlalchemy import Column, String, DateTime, Integer
from sqlalchemy.dialects.postgresql import ARRAY, UUID
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

    default_ticket_count / default_valid_dates are the allotment a guest
    of this type gets to distribute to others, unless overridden on the
    individual guest (see Guest.allotment_ticket_count). Null count means
    guests of this type are ordinary confirm/decline attendees with
    nothing to distribute.
    """

    __tablename__ = "guest_types"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    name = Column(String, nullable=False)  # e.g. "Celebrity", "Sponsor", "Volunteer", "Model"
    default_ticket_count = Column(Integer, nullable=True)
    default_valid_dates = Column(ARRAY(String), nullable=True)  # ISO date strings, e.g. "2026-06-11"
    created_at = Column(DateTime(timezone=True), server_default=func.now())