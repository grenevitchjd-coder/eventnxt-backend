import uuid

from sqlalchemy import Column, String, Integer, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class SeatingCategory(Base):
    """
    Comp-ticket allocation tier for a specific event (Front Center, Row 4,
    Standing Room, etc.), each with a capacity. This is guest ticket
    allocation by location, not general venue seating — the org's own
    scoping decision, not paid-ticket capacity (that stays the external
    ticketing platform's job). event_id is a stored reference to
    Events360's Event, not a real foreign key (separate databases).
    """

    __tablename__ = "seating_categories"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    name = Column(String, nullable=False)  # e.g. "Front Center", "Row 4", "Standing Room"
    capacity = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())