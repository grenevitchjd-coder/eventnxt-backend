import secrets

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.guest import Guest, GuestAllocationStatus
from app.models.guest_type import GuestType
from app.models.seating_category import SeatingCategory
from app.schemas.guest import GuestCreateRequest, GuestUpdateRequest, GuestResponse
from app.services.deps import CurrentUser
from app.services.event_access import require_event_access

router = APIRouter(prefix="/events/{event_id}/guests", tags=["guests"])


def _check_capacity(db: Session, event_id: str, category_id: str, exclude_guest_id=None):
    """
    Row-level lock: holds the SeatingCategory row for the rest of this
    transaction, so two simultaneous requests confirming into the same
    category can't both slip past the capacity check into the last slot —
    the second request blocks here until the first commits, then sees the
    up-to-date count. exclude_guest_id lets an update check "is there room
    for this guest" without counting the guest's own pre-existing seat
    against themselves.
    """
    category = (
        db.query(SeatingCategory)
        .filter(SeatingCategory.id == category_id, SeatingCategory.event_id == event_id)
        .with_for_update()
        .first()
    )
    if not category:
        raise HTTPException(status_code=404, detail="Seating category not found for this event.")

    query = db.query(Guest).filter(
        Guest.seating_category_id == category.id, Guest.allocation_status == GuestAllocationStatus.CONFIRMED
    )
    if exclude_guest_id:
        query = query.filter(Guest.id != exclude_guest_id)
    confirmed_count = query.count()

    if confirmed_count >= category.capacity:
        raise HTTPException(
            status_code=400,
            detail=f'"{category.name}" is at capacity ({category.capacity}/{category.capacity}).',
        )


@router.post("", response_model=GuestResponse, status_code=201)
def create_guest(
    event_id: str,
    payload: GuestCreateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    guest_type = db.query(GuestType).filter(GuestType.id == payload.guest_type_id).first()
    if not guest_type or str(guest_type.event_id) != event_id:
        raise HTTPException(status_code=404, detail="Guest type not found for this event.")

    # If the organizer didn't explicitly set a seating category, pre-fill
    # from the guest type's default (still fully overridable — this is
    # just what happens when nothing is manually specified).
    effective_seating_category_id = payload.seating_category_id or guest_type.default_seating_category_id

    if effective_seating_category_id and payload.allocation_status == "confirmed":
        _check_capacity(db, event_id, effective_seating_category_id)
    elif effective_seating_category_id:
        # Still confirm the category itself is real, just skip the
        # capacity check for a pending guest.
        category = (
            db.query(SeatingCategory)
            .filter(SeatingCategory.id == effective_seating_category_id, SeatingCategory.event_id == event_id)
            .first()
        )
        if not category:
            raise HTTPException(status_code=404, detail="Seating category not found for this event.")

    guest = Guest(
        event_id=event_id,
        name=payload.name,
        email=payload.email,
        guest_type_id=payload.guest_type_id,
        seating_category_id=effective_seating_category_id,
        allocation_status=GuestAllocationStatus(payload.allocation_status),
        rsvp_token=secrets.token_urlsafe(24),
    )
    db.add(guest)
    db.commit()
    db.refresh(guest)
    return guest


@router.get("", response_model=list[GuestResponse])
def list_guests(
    event_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    return db.query(Guest).filter(Guest.event_id == event_id).all()


@router.patch("/{guest_id}", response_model=GuestResponse)
def update_guest(
    event_id: str,
    guest_id: str,
    payload: GuestUpdateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    guest = db.query(Guest).filter(Guest.id == guest_id, Guest.event_id == event_id).first()
    if not guest:
        raise HTTPException(status_code=404, detail="Guest not found.")

    guest_type = db.query(GuestType).filter(GuestType.id == payload.guest_type_id).first()
    if not guest_type or str(guest_type.event_id) != event_id:
        raise HTTPException(status_code=404, detail="Guest type not found for this event.")

    # Only re-check capacity if this edit actually CHANGES the guest's
    # effective confirmed seat — editing just their name/email shouldn't
    # spuriously fail against a category they're already validly in, and
    # a guest's own existing seat is never counted against themselves.
    already_confirmed_here = (
        guest.allocation_status == GuestAllocationStatus.CONFIRMED
        and str(guest.seating_category_id) == str(payload.seating_category_id)
    )
    if payload.seating_category_id and payload.allocation_status == "confirmed" and not already_confirmed_here:
        _check_capacity(db, event_id, payload.seating_category_id, exclude_guest_id=guest.id)
    elif payload.seating_category_id and not already_confirmed_here:
        category = (
            db.query(SeatingCategory)
            .filter(SeatingCategory.id == payload.seating_category_id, SeatingCategory.event_id == event_id)
            .first()
        )
        if not category:
            raise HTTPException(status_code=404, detail="Seating category not found for this event.")

    guest.name = payload.name
    guest.email = payload.email
    guest.guest_type_id = payload.guest_type_id
    guest.seating_category_id = payload.seating_category_id
    guest.allocation_status = GuestAllocationStatus(payload.allocation_status)
    db.commit()
    db.refresh(guest)
    return guest


@router.delete("/{guest_id}", status_code=204)
def delete_guest(
    event_id: str,
    guest_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    guest = db.query(Guest).filter(Guest.id == guest_id, Guest.event_id == event_id).first()
    if not guest:
        raise HTTPException(status_code=404, detail="Guest not found.")
    db.delete(guest)
    db.commit()