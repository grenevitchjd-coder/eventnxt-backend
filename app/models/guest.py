import enum
import uuid

from sqlalchemy import Column, String, DateTime, Enum as SAEnum, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class GuestAllocationStatus(str, enum.Enum):
    CONFIRMED = "confirmed"
    PENDING = "pending"  # "decide hours before, based on availability" case


class Guest(Base):
    """
    A person invited to an event. Link-only access (no guest account) —
    rsvp_token is a long, cryptographically random string; holding the
    link IS the access credential, matching how the old app's rsvp_link
    worked and how most RSVP/referral products handle this.
    event_id is a stored reference to Events360's Event, not a real
    foreign key (separate databases). guest_type_id/seating_category_id
    ARE real foreign keys — those tables live in this same database.
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

    rsvp_token = Column(String, unique=True, nullable=False, index=True)
    rsvp_confirmed = Column(String, nullable=True)  # simple for now: null/pending, "yes", "no"

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    guest_type = relationship("GuestType")
    seating_category = relationship("SeatingCategory")