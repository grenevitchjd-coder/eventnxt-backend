# eventnxt-backend: app/routers/event_settings.py
"""
The event's operating profile: GET infers sensible values for events that
never chose (so shipping this changes nothing), PATCH records an explicit
choice. See app/models/event_settings.py for what the three fields mean.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.event_profile import EventProfile
from app.models.event_settings import (
    COMP_DELIVERIES,
    SALES_SOURCES,
    TICKETING_MODES,
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
    return _get_or_create(db, event_id)


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
    }
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        raise HTTPException(status_code=400, detail="Nothing to update.")
    for field, value in changes.items():
        if value not in allowed[field]:
            raise HTTPException(
                status_code=400,
                detail=f'"{value}" isn\'t a valid {field}. Allowed: {", ".join(allowed[field])}.',
            )

    settings = _get_or_create(db, event_id)
    for field, value in changes.items():
        setattr(settings, field, value)
    db.commit()
    db.refresh(settings)
    return settings