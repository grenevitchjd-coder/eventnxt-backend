import uuid

from sqlalchemy import Column, String, Text, Boolean, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class EventProfile(Base):
    """
    The rich, public-facing content for an event — title, description,
    address, banner photo, and a ticket link. This is EventNXT's job, not
    Events360's: Events360 owns the canonical event record (name, dates,
    status), EventNXT owns everything a press contact or influencer would
    actually see. event_id is a stored reference to Events360's Event, not
    a real foreign key (separate databases).

    Nothing is public until is_published is explicitly set — organizers
    opt in per event, since some events are private-guest-list-only.
    """

    __tablename__ = "event_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), nullable=False, unique=True, index=True)

    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    address = Column(String, nullable=True)
    banner_photo_url = Column(String, nullable=True)
    logo_url = Column(String, nullable=True)
    external_ticket_url = Column(String, nullable=True)

    # Events360 owns the real start/end dates. Cached here (refreshed
    # whenever the organizer loads or saves this profile, which already
    # calls Events360) so the PUBLIC page — which has no login and can't
    # call Events360's authenticated event lookup — can still show them.
    # Tradeoff: if dates change in Events360 and this profile is never
    # reopened afterward, the public page could show stale dates until it is.
    cached_start_date = Column(DateTime(timezone=True), nullable=True)
    cached_end_date = Column(DateTime(timezone=True), nullable=True)

    slug = Column(String, unique=True, nullable=False, index=True)
    is_published = Column(Boolean, nullable=False, default=False)
    published_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())