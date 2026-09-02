# eventnxt-backend: app/models/guest_ticket_request.py
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base

REQUEST_STATUSES = ("pending", "approved", "denied")


class GuestTicketRequest(Base):
    """
    An RSVP guest asking the organizer for more tickets from their own
    RSVP page — "can I bring two more?". Pending until the organizer
    approves (guest.party_size grows by quantity, extra comp tickets
    mint if the guest is already confirmed) or denies. Deliberately a
    row, not an email thread: it shows up in the Guests tab where the
    capacity picture lives.
    """

    __tablename__ = "guest_ticket_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    guest_id = Column(UUID(as_uuid=True), ForeignKey("guests.id"), nullable=False, index=True)
    quantity = Column(Integer, nullable=False)
    # Which day the extra tickets are for (day-granted guests); NULL =
    # legacy whole-party request.
    date = Column(String, nullable=True)
    note = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="pending")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)