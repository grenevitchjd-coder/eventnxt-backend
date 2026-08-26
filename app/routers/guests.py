import secrets

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.guest import Guest, GuestAllocationStatus
from app.models.guest_type import GuestType
from app.models.seating_category import SeatingCategory
from app.schemas.guest import GuestCreateRequest, GuestResponse
from app.services.deps import CurrentUser
from app.services.event_access import require_event_access

router = APIRouter(prefix="/events/{event_id}/guests", tags=["guests"])


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

    if effective_seating_category_id:
        # Row-level lock: holds the SeatingCategory row for the rest of this
        # transaction, so two simultaneous requests confirming into the same
        # category can't both slip past the capacity check into the last
        # slot — the second request blocks here until the first commits,
        # then sees the up-to-date count.
        category = (
            db.query(SeatingCategory)
            .filter(SeatingCategory.id == effective_seating_category_id, SeatingCategory.event_id == event_id)
            .with_for_update()
            .first()
        )
        if not category:
            raise HTTPException(status_code=404, detail="Seating category not found for this event.")

        if payload.allocation_status == "confirmed":
            confirmed_count = (
                db.query(Guest)
                .filter(
                    Guest.seating_category_id == category.id,
                    Guest.allocation_status == GuestAllocationStatus.CONFIRMED,
                )
                .count()
            )
            if confirmed_count >= category.capacity:
                raise HTTPException(
                    status_code=400,
                    detail=f'"{category.name}" is at capacity ({category.capacity}/{category.capacity}).',
                )

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