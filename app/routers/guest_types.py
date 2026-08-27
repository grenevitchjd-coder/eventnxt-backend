from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.guest import Guest
from app.models.guest_type import GuestType
from app.models.seating_category import SeatingCategory
from app.schemas.guest_type import GuestTypeCreateRequest, GuestTypeUpdateRequest, GuestTypeResponse
from app.services.deps import CurrentUser
from app.services.event_access import require_event_access

router = APIRouter(prefix="/events/{event_id}/guest-types", tags=["guest-types"])


def _validate_default_seating(db: Session, event_id: str, seating_category_id):
    if not seating_category_id:
        return
    category = (
        db.query(SeatingCategory)
        .filter(SeatingCategory.id == seating_category_id, SeatingCategory.event_id == event_id)
        .first()
    )
    if not category:
        raise HTTPException(status_code=404, detail="That seating category doesn't belong to this event.")


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
    _validate_default_seating(db, event_id, payload.default_seating_category_id)

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


@router.patch("/{guest_type_id}", response_model=GuestTypeResponse)
def update_guest_type(
    event_id: str,
    guest_type_id: str,
    payload: GuestTypeUpdateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    guest_type = (
        db.query(GuestType).filter(GuestType.id == guest_type_id, GuestType.event_id == event_id).first()
    )
    if not guest_type:
        raise HTTPException(status_code=404, detail="Guest type not found.")

    _validate_default_seating(db, event_id, payload.default_seating_category_id)

    guest_type.name = payload.name
    guest_type.default_seating_category_id = payload.default_seating_category_id
    db.commit()
    db.refresh(guest_type)
    return guest_type


@router.delete("/{guest_type_id}", status_code=204)
def delete_guest_type(
    event_id: str,
    guest_type_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    guest_type = (
        db.query(GuestType).filter(GuestType.id == guest_type_id, GuestType.event_id == event_id).first()
    )
    if not guest_type:
        raise HTTPException(status_code=404, detail="Guest type not found.")

    guest_count = db.query(Guest).filter(Guest.guest_type_id == guest_type_id).count()
    if guest_count > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Can't delete — {guest_count} guest(s) are still using this type. "
            f"Reassign or remove them first.",
        )

    db.delete(guest_type)
    db.commit()