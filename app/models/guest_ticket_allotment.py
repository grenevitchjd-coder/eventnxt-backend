import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class GuestTicketAllotment(Base):
    """
    A per-guest override of their guest type's default ticket allotment —
    e.g. bumping one A-list model to 3 Friday tickets while the rest of
    the Model type stays at the default. Only meaningful when
    Guest.ticket_allotment_overridden is True; see
    app/services/seating.py's effective_allotment() for how override vs.
    type-default is resolved. A guest created via someone else's
    distribution (allocated_by_guest_id set) never has rows here — see
    the note on that field for why.
    """

    __tablename__ = "guest_ticket_allotments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    guest_id = Column(UUID(as_uuid=True), ForeignKey("guests.id"), nullable=False)
    date = Column(String, nullable=False)  # ISO date string, e.g. "2026-06-11"
    quantity = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())