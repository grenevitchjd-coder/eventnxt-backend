"""eventnxt-backend: app/models/ticket.py"""

import enum
import uuid

from sqlalchemy import Column, DateTime, Enum as SAEnum, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class TicketStatus(str, enum.Enum):
    VALID = "valid"
    REFUNDED = "refunded"


class Ticket(Base):
    """
    One admission. An order for 4 GA tickets produces 4 rows here, each
    with its own unique code — slightly more granular than v1 strictly
    needs, but it's what makes per-ticket check-in / QR scanning a later
    feature instead of a painful migration.

    seat_id is the deliberate hook for the future per-seat world
    (theater-style selection, PlanNXT layouts): a plain nullable UUID
    with NO foreign key, because no seats table exists yet — when it
    does, the FK arrives with it and every existing ticket is simply
    "no assigned seat". Until then this column costs nothing.
    """

    __tablename__ = "tickets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
# Order lineage — null for comp tickets (which carry guest_id instead).
    # A ticket has one parent or the other, never neither: enforced at the
    # two mint sites (checkout fulfillment / comp issuance).
    order_id = Column(UUID(as_uuid=True), ForeignKey("orders.id"), nullable=True, index=True)
    order_item_id = Column(UUID(as_uuid=True), ForeignKey("order_items.id"), nullable=True)
    ticket_type_id = Column(UUID(as_uuid=True), ForeignKey("ticket_types.id"), nullable=True, index=True)
    # Comp lineage — the guest this admission belongs to.
    guest_id = Column(UUID(as_uuid=True), ForeignKey("guests.id"), nullable=True, index=True)
    event_id = Column(UUID(as_uuid=True), nullable=False, index=True)  # denormalized for door-day lookups

    code = Column(String, nullable=False, unique=True, index=True)  # human-readable unique admission code
    status = Column(SAEnum(TicketStatus), nullable=False, default=TicketStatus.VALID)

    seat_id = Column(UUID(as_uuid=True), ForeignKey("seats.id", ondelete="SET NULL"), nullable=True, index=True)  # the assigned seat this code admits to (comps and GA leave it null)
    # Set once at the door — the moment this code admitted its person.
    # The day this code admits on (ISO string). NULL = any day — every
    # pre-0032 ticket, and comps without a visit date.
    valid_date = Column(String, nullable=True)
    checked_in_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())