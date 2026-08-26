from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.guest_type import GuestType
from app.models.seating_category import SeatingCategory
from app.schemas.guest_type import GuestTypeCreateRequest, GuestTypeResponse
from app.services.deps import CurrentUser
from app.services.event_access import require_event_access

router = APIRouter(prefix="/events/{event_id}/guest-types", tags=["guest-types"])


@router.post("", response_model=GuestTypeResponse, status_code=201)
def create_guest_type(
    event_id: str,
    payload: GuestTypeCreateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    """
    Event-scoped — different events for the same org can define their own
    guest types, since who gets invited varies event to event.
    """
    if payload.default_seating_category_id:
        category = (
            db.query(SeatingCategory)
            .filter(
                SeatingCategory.id == payload.default_seating_category_id,
                SeatingCategory.event_id == event_id,
            )
            .first()
        )
        if not category:
            raise HTTPException(
                status_code=404, detail="That seating category doesn't belong to this event."
            )

    guest_type = GuestType(
        event_id=event_id, name=payload.name, default_seating_category_id=payload.default_seating_category_id
    )
    db.add(guest_type)
    db.commit()
    db.refresh(guest_type)
    return guest_type


@router.get("", response_model=list[GuestTypeResponse])
def list_guest_types(
    event_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(require_event_access)
):
    return db.query(GuestType).filter(GuestType.event_id == event_id).all()