# eventnxt-backend: app/models/guest_type.py
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
    GuestTypeSeatingPriority (an ordered list) and its default ticket
    allotment lives in GuestTypeTicketAllotment (one row per day) — see
    those models for why each is its own table rather than a column here.
    event_id is a stored reference to Events360's Event, not a real
    foreign key (separate databases).
    """

    __tablename__ = "guest_types"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    name = Column(String, nullable=False)

    # Default guest experience for this type ('invite' | 'distribute' |
    # 'select'); NULL = derived from allotments the legacy way. A guest's
    # own guest_mode overrides this.
    guest_mode = Column(String, nullable=True)  # e.g. "Celebrity", "Sponsor", "Volunteer", "Model"
    created_at = Column(DateTime(timezone=True), server_default=func.now())