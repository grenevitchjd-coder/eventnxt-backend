# eventnxt-backend: app/models/guest.py
import enum
import uuid

from sqlalchemy import Boolean, Column, String, DateTime, Enum as SAEnum, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
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

    Some guests (models, sponsors) hold an ALLOTMENT — a per-day pool of
    tickets they distribute to others themselves via their RSVP page,
    rather than just confirming their own attendance. The allotment
    itself lives in GuestTicketAllotment rows (one per day, since
    "10 Thursday, 5 Saturday" are genuinely separate pools, not one
    shared number) — ticket_allotment_overridden says whether to use
    THIS guest's own rows or fall back to their guest type's default
    rows (GuestTypeTicketAllotment). A guest created via someone else's
    distribution (allocated_by_guest_id set) is always treated as having
    NO allotment of their own, regardless of this flag or their type's
    default — see app/services/seating.py's effective_allotment() —
    because distribution is one level deep on purpose: a delegated
    recipient just RSVPs for themselves, they don't get to redistribute
    further.

    Each ticket a distributor hands out becomes its own Guest row —
    allocated_by_guest_id links it back to whoever distributed it,
    party_size is how many of the allotment that one line consumed
    (default 1, but a distributor can put more than one ticket under a
    single name), and visit_date is which specific day THIS guest's own
    ticket is for.
    """

    __tablename__ = "guests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), nullable=False, index=True)

    name = Column(String, nullable=False)
    email = Column(String, nullable=False, index=True)

    guest_type_id = Column(UUID(as_uuid=True), ForeignKey("guest_types.id"), nullable=False)
    # Nullable: a guest type may have no seating category assigned yet.
    seating_category_id = Column(UUID(as_uuid=True), ForeignKey("seating_categories.id"), nullable=True)
    # Which section within their pool this comp guest was placed in
    # (resolver or organizer). A label, not an FK — see the priority
    # model. NULL = floats at pool level (every pre-0031 comp).
    section_label = Column(String, nullable=True)

    allocation_status = Column(
        SAEnum(
            GuestAllocationStatus,
            name="guest_allocation_status",
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
        default=GuestAllocationStatus.CONFIRMED,
    )

    # True = use this guest's own GuestTicketAllotment rows (even if
    # there are none, meaning "explicitly no tickets to distribute").
    # False = inherit the guest type's default rows entirely.
    ticket_allotment_overridden = Column(Boolean, nullable=False, default=False)

    # How many tickets/seats THIS guest record itself consumes (default
    # 1 — a distributor can bump this for a single named recipient
    # instead of creating duplicate rows under the same name).
    party_size = Column(Integer, nullable=False, default=1)

    # When this guest's heads block public sales: 'now' (pending +
    # confirmed count — the default), 'on_confirm', or 'later'.
    hold_timing = Column(String, nullable=False, default="now")

    # Distributing parents: do same-day recipients from this allocation
    # sit together (one section, side by side) or spread individually?
    cohort_together = Column(Boolean, nullable=False, default=True)
    # Which specific day this guest's own ticket/attendance is for.
    visit_date = Column(String, nullable=True)
    # Set when this guest was created by someone else's distribution —
    # links back to the allotment holder who gave them the ticket.
    allocated_by_guest_id = Column(UUID(as_uuid=True), ForeignKey("guests.id"), nullable=True)

    # Explicit guest experience: 'invite' | 'distribute' | 'select'.
    # NULL = derived the legacy way (allotment holder -> distribute,
    # otherwise invite). This per-guest value overrides the type default.
    guest_mode = Column(String, nullable=True)

    # RSVP said yes but no section could seat them — the yes is recorded,
    # allocation stays PENDING (capacity math never counts a phantom
    # seat), and this flag is the organizer's Needs-seating queue.
    needs_seating = Column(Boolean, nullable=False, default=False)

    rsvp_token = Column(String, unique=True, nullable=False, index=True)
    rsvp_confirmed = Column(String, nullable=True)  # simple for now: null/pending, "yes", "no"

    # Set (to the send time) when the organizer marks this guest's RSVP
    # link as sent — manual, since there's no automated email yet. Null
    # means "not sent." Doubles as a boolean via the null check while
    # also recording when, for follow-up purposes.
    link_sent_at = Column(DateTime(timezone=True), nullable=True)

    # Free-text extras an organizer might track per guest — comp items
    # beyond the ticket itself (drinks, a gift bag) and general notes.
    # Deliberately plain strings, not structured data — these are for
    # human reading, not computation.
    perks = Column(String, nullable=True)
    comments = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    guest_type = relationship("GuestType")
    seating_category = relationship("SeatingCategory")
    allocated_by = relationship("Guest", remote_side=[id])