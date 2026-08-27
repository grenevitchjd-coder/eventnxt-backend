import secrets

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.guest import Guest, GuestAllocationStatus
from app.models.guest_type import GuestType
from app.models.guest_type_seating_priority import GuestTypeSeatingPriority
from app.models.seating_category import SeatingCategory
from app.schemas.guest import GuestCreateRequest, GuestUpdateRequest, GuestResponse
from app.services import seating
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

    if payload.seating_category_id:
        # Explicit override — use it directly, same single-category check
        # as before, regardless of what the guest type's priority list says.
        effective_seating_category_id = payload.seating_category_id
        if payload.allocation_status == "confirmed":
            seating.check_capacity(db, event_id, effective_seating_category_id, party_size=payload.party_size)
        else:
            category = (
                db.query(SeatingCategory)
                .filter(SeatingCategory.id == effective_seating_category_id, SeatingCategory.event_id == event_id)
                .first()
            )
            if not category:
                raise HTTPException(status_code=404, detail="Seating category not found for this event.")

    elif payload.allocation_status == "confirmed":
        # Nothing explicit — walk the guest type's priority list for the
        # first category with enough room.
        effective_seating_category_id = seating.resolve_seating_from_priorities(
            db, event_id, payload.guest_type_id, party_size=payload.party_size
        )
        if effective_seating_category_id is None:
            if seating.has_seating_priorities(db, payload.guest_type_id):
                raise HTTPException(
                    status_code=400,
                    detail="All preferred seating categories for this guest type are full.",
                )
            # else: no priorities configured at all — guest is simply unassigned, that's fine

    else:
        # Pending/declined, nothing explicit — use the top priority as a
        # placeholder (no capacity check needed, nothing is held yet, so
        # this is just "which category would they land in first" — not a
        # search for one with room).
        first_priority = (
            db.query(GuestTypeSeatingPriority)
            .filter(GuestTypeSeatingPriority.guest_type_id == payload.guest_type_id)
            .order_by(GuestTypeSeatingPriority.priority_order)
            .first()
        )
        effective_seating_category_id = first_priority.seating_category_id if first_priority else None

    guest = Guest(
        event_id=event_id,
        name=payload.name,
        email=payload.email,
        guest_type_id=payload.guest_type_id,
        seating_category_id=effective_seating_category_id,
        allocation_status=GuestAllocationStatus(payload.allocation_status),
        party_size=payload.party_size,
        allotment_ticket_count=payload.allotment_ticket_count,
        allotment_valid_dates=payload.allotment_valid_dates,
        visit_date=payload.visit_date,
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
    """
    Editing always takes an explicit seating_category_id (or null) — no
    priority-list magic here, unlike creation. Someone editing a specific
    existing guest is making a deliberate choice, not asking the system
    to decide for them.
    """
    guest = db.query(Guest).filter(Guest.id == guest_id, Guest.event_id == event_id).first()
    if not guest:
        raise HTTPException(status_code=404, detail="Guest not found.")

    guest_type = db.query(GuestType).filter(GuestType.id == payload.guest_type_id).first()
    if not guest_type or str(guest_type.event_id) != event_id:
        raise HTTPException(status_code=404, detail="Guest type not found for this event.")

    already_confirmed_here = (
        guest.allocation_status == GuestAllocationStatus.CONFIRMED
        and str(guest.seating_category_id) == str(payload.seating_category_id)
        and guest.party_size == payload.party_size
    )
    if payload.seating_category_id and payload.allocation_status == "confirmed" and not already_confirmed_here:
        seating.check_capacity(
            db, event_id, payload.seating_category_id, party_size=payload.party_size, exclude_guest_id=guest.id
        )
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
    guest.party_size = payload.party_size
    guest.allotment_ticket_count = payload.allotment_ticket_count
    guest.allotment_valid_dates = payload.allotment_valid_dates
    guest.visit_date = payload.visit_date
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