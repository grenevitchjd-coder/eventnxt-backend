import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class GuestTypeTicketAllotment(Base):
    """
    One day's worth of the default ticket allotment for a guest type —
    "Sponsors get 10 tickets Thursday, 10 Friday, 5 Saturday" is three
    rows, each a SEPARATE pool (using up the 10 Thursday tickets never
    touches the Saturday 5). Replaces the earlier single
    ticket_count + list-of-valid-dates, which couldn't express different
    quantities on different days. A guest of this type inherits these
    rows unless they have their own override rows (GuestTicketAllotment).
    """

    __tablename__ = "guest_type_ticket_allotments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    guest_type_id = Column(UUID(as_uuid=True), ForeignKey("guest_types.id"), nullable=False)
    date = Column(String, nullable=False)  # ISO date string, e.g. "2026-06-11"
    quantity = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())