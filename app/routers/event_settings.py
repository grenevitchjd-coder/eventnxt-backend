# eventnxt-backend: app/routers/event_settings.py
"""
The event's operating profile: GET infers sensible values for events that
never chose (so shipping this changes nothing), PATCH records an explicit
choice. See app/models/event_settings.py for what the three fields mean.
"""

from datetime import date

from dateutil import parser as date_parser

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.event_profile import EventProfile
from app.models.event_settings import (
    COMP_DELIVERIES,
    PRICING_MODES,
    SALES_SOURCES,
    SEATING_MODES,
    TICKETING_MODES,
    TICKET_SPANS,
    EventSettings,
)
from app.models.ticket_type import TicketType
from app.schemas.event_settings import EventSettingsResponse, EventSettingsUpdateRequest
from app.services.deps import CurrentUser
from app.services.event_access import require_event_access

router = APIRouter(tags=["event-settings"])


def _infer_defaults(db: Session, event_id: str) -> dict:
    """
    What this event ALREADY behaves like, read from what exists:
    native ticket types -> 'native'; an external ticket link on the
    profile -> 'external'; neither -> 'native' (the neutral starting
    point for a fresh event — nothing renders differently until ticket
    types or a link actually exist).
    """
    has_native = (
        db.query(TicketType.id).filter(TicketType.event_id == event_id).limit(1).first() is not None
    )
    if has_native:
        return {"ticketing_mode": "native", "sales_source": "native", "comp_delivery": "rsvp_required"}

    profile = db.query(EventProfile).filter(EventProfile.event_id == event_id).first()
    if profile and profile.external_ticket_url:
        return {"ticketing_mode": "external", "sales_source": "csv", "comp_delivery": "rsvp_required"}

    return {"ticketing_mode": "native", "sales_source": "native", "comp_delivery": "rsvp_required"}


def _event_day_bounds(user: CurrentUser):
    """
    The event's (first_day, last_day) as ISO strings, straight from the
    Events360 payload require_event_access already fetched — the same
    source the profile caches its dates from. (None, None) when Events360
    has no dates yet, in which case the manual inputs still work.
    """
    data = getattr(user, "event_data", None) or {}
    try:
        first = date_parser.isoparse(data["start_date"]).date().isoformat() if data.get("start_date") else None
        last = date_parser.isoparse(data["end_date"]).date().isoformat() if data.get("end_date") else None
    except (ValueError, TypeError):
        return None, None
    return first, last


def _sync_days_from_events360(settings: EventSettings, user: CurrentUser) -> bool:
    """
    Events360 is the authority on WHEN the event runs; the span setting
    only decides how tickets work across those days. One-day events are
    forced back to single_day span (nothing to configure); multi-day
    events get first/last kept in step with Events360 so a date change
    there self-heals here on the next read. Returns whether anything
    changed (caller commits).
    """
    first, last = _event_day_bounds(user)
    if not (first and last):
        return False
    changed = False
    if first == last:
        if settings.ticket_span != "single_day" or settings.first_day or settings.last_day:
            settings.ticket_span = "single_day"
            settings.first_day = None
            settings.last_day = None
            changed = True
        return changed
    if settings.first_day != first or settings.last_day != last:
        settings.first_day = first
        settings.last_day = last
        changed = True
    return changed


def _get_or_create(db: Session, event_id: str) -> EventSettings:
    settings = db.query(EventSettings).filter(EventSettings.event_id == event_id).first()
    if settings:
        return settings
    settings = EventSettings(event_id=event_id, **_infer_defaults(db, event_id))
    db.add(settings)
    db.commit()
    db.refresh(settings)
    return settings


@router.get("/events/{event_id}/settings", response_model=EventSettingsResponse)
def get_settings(
    event_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    settings = _get_or_create(db, event_id)
    if _sync_days_from_events360(settings, user):
        db.commit()
        db.refresh(settings)
    return settings


@router.patch("/events/{event_id}/settings", response_model=EventSettingsResponse)
def update_settings(
    event_id: str,
    payload: EventSettingsUpdateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    allowed = {
        "ticketing_mode": TICKETING_MODES,
        "sales_source": SALES_SOURCES,
        "comp_delivery": COMP_DELIVERIES,
        "ticket_span": TICKET_SPANS,
        "pricing_mode": PRICING_MODES,
        "seating_mode": SEATING_MODES,
    }
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        raise HTTPException(status_code=400, detail="Nothing to update.")
    for field, value in changes.items():
        if field in ("first_day", "last_day"):
            try:
                date.fromisoformat(value)
            except ValueError:
                raise HTTPException(status_code=400, detail=f'"{value}" isn\'t a date (use YYYY-MM-DD).')
            continue
        if value not in allowed[field]:
            raise HTTPException(
                status_code=400,
                detail=f'"{value}" isn\'t a valid {field}. Allowed: {", ".join(allowed[field])}.',
            )

    settings = _get_or_create(db, event_id)
    ev_first, ev_last = _event_day_bounds(user)
    if ev_first and ev_last and ev_first == ev_last and changes.get("ticket_span") not in (None, "single_day"):
        raise HTTPException(
            status_code=400,
            detail=f"Events360 has this as a one-day event ({ev_first}) — multi-day spans need the event's dates changed there first.",
        )
    # Cross-field guards evaluated against the MERGED state — Events360's
    # dates fill first/last automatically when it knows them, so span and
    # days can arrive in one patch, across two, or days not at all.
    merged = {
        "ticket_span": changes.get("ticket_span", settings.ticket_span),
        "first_day": (ev_first if ev_first and ev_last and ev_first != ev_last else None)
        or changes.get("first_day", settings.first_day),
        "last_day": (ev_last if ev_first and ev_last and ev_first != ev_last else None)
        or changes.get("last_day", settings.last_day),
    }
    if merged["first_day"] and merged["last_day"] and merged["first_day"] > merged["last_day"]:
        raise HTTPException(status_code=400, detail="First day must be on or before the last day.")
    if merged["ticket_span"] != "single_day" and not (merged["first_day"] and merged["last_day"]):
        raise HTTPException(
            status_code=400,
            detail="Multi-day and mixed spans need the event's first and last day set (so tickets know their dates).",
        )
    for field, value in changes.items():
        setattr(settings, field, value)
    _sync_days_from_events360(settings, user)
    db.commit()
    db.refresh(settings)
    return settings