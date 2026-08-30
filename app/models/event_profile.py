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

    # ---- Public-page personalization ----
    # All nullable, and null always means "the original default look" —
    # so every profile that existed before these columns renders exactly
    # as it always has, without any backfill.
    font_family = Column(String, nullable=True)  # display font; null = Fraunces (the original)
    logo_position = Column(String, nullable=True)  # null/centered | top-left | top-center | top-right | hidden
    banner_focus = Column(String, nullable=True)  # which part of the banner survives the crop: null/center | top | bottom
    about_us = Column(Text, nullable=True)  # optional "About Us" section near the bottom of the page

    # ---- Ticketing-adjacent public content ----
    # refund_policy: shown at checkout, in the organizer's own words —
    # what's displayed at purchase is what protects them in a dispute.
    # venue_map_url: an uploaded image of the venue/seating map (same
    # storage pipeline as banner/logo) — the honest v1 of "help buyers
    # see what they're buying"; the interactive map replaces it someday.
    # venue_layout: JSON home for future structured layout data (the
    # PlanNXT interchange) — costs nothing until something fills it.
    refund_policy = Column(Text, nullable=True)
    venue_map_url = Column(String, nullable=True)
    venue_layout = Column(Text, nullable=True)  # JSON as text; parsed by whoever consumes it

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