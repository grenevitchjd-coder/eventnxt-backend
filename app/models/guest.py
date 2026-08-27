import enum
import uuid

from sqlalchemy import Column, String, DateTime, Enum as SAEnum, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class GuestAllocationStatus(str, enum.Enum):
    CONFIRMED = "confirmed"
    PENDING = "pending"  # "decide hours before, based on availability" case
    DECLINED = "declined"  # guest said no on their RSVP page — frees any held seat


class Guest(Base):
    """
    A person invited to an event. Link-only access (no guest account) —
    rsvp_token is a long, cryptographically random string; holding the
    link IS the access credential, matching how the old app's rsvp_link
    worked and how most RSVP/referral products handle this.
    event_id is a stored reference to Events360's Event, not a real
    foreign key (separate databases). guest_type_id/seating_category_id
    ARE real foreign keys — those tables live in this same database.

    Some guests (models, sponsors) hold an ALLOTMENT — a pool of tickets
    they distribute to others themselves via their RSVP page, rather
    than just confirming their own attendance. allotment_ticket_count /
    allotment_valid_dates describe that pool (null on an ordinary guest
    who has nothing to distribute). Each ticket they hand out becomes
    its own Guest row — allocated_by_guest_id links it back to whoever
    distributed it, party_size is how many of the allotment that one
    line consumed (default 1, but a distributor can put more than one
    ticket under a single name), and visit_date is which day THIS
    guest's own ticket is for. Distribution is one level deep on
    purpose — a delegated recipient just RSVPs for themselves, they
    don't get their own allotment to redistribute further.
    """

    __tablename__ = "guests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), nullable=False, index=True)

    name = Column(String, nullable=False)
    email = Column(String, nullable=False, index=True)

    guest_type_id = Column(UUID(as_uuid=True), ForeignKey("guest_types.id"), nullable=False)
    # Nullable: a guest type may have no seating category assigned yet.
    seating_category_id = Column(UUID(as_uuid=True), ForeignKey("seating_categories.id"), nullable=True)

    allocation_status = Column(
        SAEnum(
            GuestAllocationStatus,
            name="guest_allocation_status",
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
        default=GuestAllocationStatus.CONFIRMED,
    )

    # How many tickets THIS guest can distribute to others, and which
    # dates those tickets are valid for. Null count = not an allotment
    # holder, just an ordinary confirm/decline guest.
    allotment_ticket_count = Column(Integer, nullable=True)
    allotment_valid_dates = Column(ARRAY(String), nullable=True)  # ISO date strings, e.g. "2026-06-11"

    # How many tickets/seats THIS guest record itself consumes (default
    # 1 — a distributor can bump this for a single named recipient
    # instead of creating duplicate rows under the same name).
    party_size = Column(Integer, nullable=False, default=1)
    # Which specific day this guest's own ticket/attendance is for.
    visit_date = Column(String, nullable=True)
    # Set when this guest was created by someone else's distribution —
    # links back to the allotment holder who gave them the ticket.
    allocated_by_guest_id = Column(UUID(as_uuid=True), ForeignKey("guests.id"), nullable=True)

    rsvp_token = Column(String, unique=True, nullable=False, index=True)
    rsvp_confirmed = Column(String, nullable=True)  # simple for now: null/pending, "yes", "no"

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    guest_type = relationship("GuestType")
    seating_category = relationship("SeatingCategory")
    allocated_by = relationship("Guest", remote_side=[id])