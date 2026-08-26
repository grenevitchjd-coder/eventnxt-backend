import uuid

from sqlalchemy import Column, String, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class GuestType(Base):
    """
    Event-scoped (Celebrity, Sponsor, Volunteer, Model, etc.) — different
    events for the same org can define their own guest types, since who
    an org invites varies event to event. Carries a DEFAULT seating
    allocation that pre-fills when a guest of this type is added, freely
    overridable per person (e.g. volunteers default to Row 4, but one
    specific volunteer gets manually bumped to Row 1).
    event_id is a stored reference to Events360's Event, not a real
    foreign key (separate databases). default_seating_category_id IS a
    real foreign key — SeatingCategory lives in this same database.
    """

    __tablename__ = "guest_types"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    name = Column(String, nullable=False)  # e.g. "Celebrity", "Sponsor", "Volunteer", "Model"
    default_seating_category_id = Column(
        UUID(as_uuid=True), ForeignKey("seating_categories.id"), nullable=True
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())