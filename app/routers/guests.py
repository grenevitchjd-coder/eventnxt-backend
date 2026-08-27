import secrets

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.guest import Guest, GuestAllocationStatus
from app.models.guest_type import GuestType
from app.models.guest_type_seating_priority import GuestTypeSeatingPriority
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


def _resolve_seating_from_priorities(db: Session, event_id: str, guest_type_id: str):
    """
    Walks the guest type's ORDERED seating priority list and returns the
    first category id with room, or None (either no priorities are
    configured, or every one is full).

    Deadlock safety: a guest creation may need to lock several categories
    in one transaction. If we locked them in PRIORITY order, two
    concurrent creations with overlapping priority lists in different
    orders could deadlock (transaction A holds category X waiting on Y,
    transaction B holds Y waiting on X — a circular wait). Instead every
    transaction locks the same set of candidate categories in a fixed,
    deterministic order (by id) regardless of priority — since every
    transaction acquires locks in that identical sequence, a circular
    wait, and therefore a deadlock, can never form. The actual business
    decision (which category to pick) is made afterward, using the
    already-locked data, walking the REAL priority order.
    """
    priorities = (
        db.query(GuestTypeSeatingPriority)
        .filter(GuestTypeSeatingPriority.guest_type_id == guest_type_id)
        .order_by(GuestTypeSeatingPriority.priority_order)
        .all()
    )
    if not priorities:
        return None

    category_ids = [p.seating_category_id for p in priorities]

    locked_categories = (
        db.query(SeatingCategory)
        .filter(SeatingCategory.id.in_(category_ids), SeatingCategory.event_id == event_id)
        .order_by(SeatingCategory.id)  # fixed lock order — NOT priority order
        .with_for_update()
        .all()
    )
    categories_by_id = {c.id: c for c in locked_categories}

    for p in priorities:  # now walk the REAL priority order to decide
        category = categories_by_id.get(p.seating_category_id)
        if not category:
            continue
        confirmed_count = (
            db.query(Guest)
            .filter(
                Guest.seating_category_id == category.id,
                Guest.allocation_status == GuestAllocationStatus.CONFIRMED,
            )
            .count()
        )
        if confirmed_count < category.capacity:
            return category.id

    return None


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
            _check_capacity(db, event_id, effective_seating_category_id)
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
        # first available category.
        effective_seating_category_id = _resolve_seating_from_priorities(db, event_id, payload.guest_type_id)
        if effective_seating_category_id is None:
            has_priorities = (
                db.query(GuestTypeSeatingPriority)
                .filter(GuestTypeSeatingPriority.guest_type_id == payload.guest_type_id)
                .count()
                > 0
            )
            if has_priorities:
                raise HTTPException(
                    status_code=400,
                    detail="All preferred seating categories for this guest type are full.",
                )
            # else: no priorities configured at all — guest is simply unassigned, that's fine

    else:
        # Pending guest, nothing explicit — use the top priority as a
        # placeholder (no capacity check needed while pending).
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