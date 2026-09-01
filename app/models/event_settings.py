# eventnxt-backend: app/models/event_settings.py
import uuid

from sqlalchemy import Column, DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base

# The three operating-profile choices. Kept as plain strings (not PG enums)
# so adding a value later is a code change, not a migration — same tradeoff
# the profile's logo_position / banner_focus columns already made.
TICKETING_MODES = ("native", "external", "invite_only")
SALES_SOURCES = ("native", "csv", "api")
COMP_DELIVERIES = ("rsvp_required", "auto_send")
# Multi-day declarations (slice 1: only ticket_span changes behavior;
# pricing_mode and seating_mode shape the slice-2 composer).
# single_day = a one-day event (today's behavior, no day machinery).
# multi_day  = one ticket covers every day (one dated code per day).
# per_day    = a multi-day event selling tickets day by day (slice 2).
# mixed      = per-day tickets AND whole-event passes side by side.
TICKET_SPANS = ("single_day", "multi_day", "per_day", "mixed")
PRICING_MODES = ("uniform", "per_day")
SEATING_MODES = ("uniform", "per_day")


class EventSettings(Base):
    """
    The event's declared OPERATING PROFILE — the choices that decide how
    the rest of the app should behave for this event:

      ticketing_mode  'native'      selling through EventNXT/Stripe
                      'external'    selling on Eventbrite/etc via link-out
                      'invite_only' no public sales at all, comps only
      sales_source    'native'      sales numbers come from our orders
                      'csv'         organizer imports from their platform
                      'api'         partner API feed (future)
      comp_delivery   'rsvp_required'  comp tickets send after the guest
                                       RSVPs yes (capacity permitting)
                      'auto_send'      comp tickets send the moment the
                                       guest is added

    Separate table from event_profiles on purpose: a profile only exists
    once the organizer designs their public page, but the operating
    profile matters from the moment the event exists. A missing row means
    "never explicitly chosen" — the settings endpoint infers sensible
    values from what the event already does, so shipping this changes
    nothing for existing events.

    event_id references Events360's Event (no real FK — separate
    databases), same as everywhere else.
    """

    __tablename__ = "event_settings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), nullable=False, unique=True, index=True)

    ticketing_mode = Column(String, nullable=False, default="native")
    sales_source = Column(String, nullable=False, default="native")
    comp_delivery = Column(String, nullable=False, default="rsvp_required")

    # Multi-day: span decides whether whole-event purchases fan out to
    # one dated code per event day; first/last day (ISO strings, same
    # dialect as guests.visit_date) define the day list.
    ticket_span = Column(String, nullable=False, default="single_day")
    pricing_mode = Column(String, nullable=False, default="uniform")
    seating_mode = Column(String, nullable=False, default="uniform")
    first_day = Column(String, nullable=True)
    last_day = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)