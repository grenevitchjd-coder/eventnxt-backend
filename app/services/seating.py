from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.guest import Guest, GuestAllocationStatus
from app.models.guest_type import GuestType
from app.models.guest_type_seating_priority import GuestTypeSeatingPriority
from app.models.seating_category import SeatingCategory


def check_capacity(db: Session, event_id: str, category_id: str, party_size: int = 1, exclude_guest_id=None):
    """
    Row-level lock: holds the SeatingCategory row for the rest of this
    transaction, so two simultaneous requests confirming into the same
    category can't both slip past the capacity check into the last slot —
    the second request blocks here until the first commits, then sees the
    up-to-date total. exclude_guest_id lets an update check "is there room
    for this guest" without counting the guest's own pre-existing seat
    against themselves.

    Counts by SUM(party_size), not row count — one guest record can hold
    more than one seat (a distributor giving several of their tickets to
    a single named recipient), so capacity has to track seats, not people.
    """
    category = (
        db.query(SeatingCategory)
        .filter(SeatingCategory.id == category_id, SeatingCategory.event_id == event_id)
        .with_for_update()
        .first()
    )
    if not category:
        raise HTTPException(status_code=404, detail="Seating category not found for this event.")

    query = db.query(func.coalesce(func.sum(Guest.party_size), 0)).filter(
        Guest.seating_category_id == category.id, Guest.allocation_status == GuestAllocationStatus.CONFIRMED
    )
    if exclude_guest_id:
        query = query.filter(Guest.id != exclude_guest_id)
    confirmed_total = query.scalar() or 0

    if confirmed_total + party_size > category.capacity:
        remaining = max(category.capacity - confirmed_total, 0)
        raise HTTPException(
            status_code=400,
            detail=f'"{category.name}" only has {remaining} seat(s) left (capacity {category.capacity}) — '
            f"not enough for {party_size}.",
        )


def resolve_seating_from_priorities(db: Session, event_id: str, guest_type_id: str, party_size: int = 1):
    """
    Walks the guest type's ORDERED seating priority list and returns the
    first category id with room for `party_size` more seats, or None
    (either no priorities are configured, or none has enough room).

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
        confirmed_total = (
            db.query(func.coalesce(func.sum(Guest.party_size), 0))
            .filter(
                Guest.seating_category_id == category.id,
                Guest.allocation_status == GuestAllocationStatus.CONFIRMED,
            )
            .scalar()
            or 0
        )
        if confirmed_total + party_size <= category.capacity:
            return category.id

    return None


def has_seating_priorities(db: Session, guest_type_id: str) -> bool:
    return (
        db.query(GuestTypeSeatingPriority)
        .filter(GuestTypeSeatingPriority.guest_type_id == guest_type_id)
        .count()
        > 0
    )


def effective_allotment(guest: Guest, guest_type: GuestType):
    """A guest's own allotment override, falling back to the guest type's default."""
    ticket_count = (
        guest.allotment_ticket_count if guest.allotment_ticket_count is not None else guest_type.default_ticket_count
    )
    valid_dates = (
        guest.allotment_valid_dates if guest.allotment_valid_dates is not None else guest_type.default_valid_dates
    )
    return ticket_count, valid_dates


def check_allotment_capacity(db: Session, parent_guest_id: str, additional_total: int, total_allotment: int):
    """
    Row-level lock on the PARENT guest (the model/sponsor distributing
    tickets) — holds it for the rest of this transaction so two
    simultaneous distribution submissions from the same person can't both
    slip past the remaining-tickets check. Only ever locks one row per
    transaction (the parent), so unlike seating there's no multi-row
    lock-ordering to worry about — different parents' distributions never
    contend with each other.
    """
    db.query(Guest).filter(Guest.id == parent_guest_id).with_for_update().first()

    distributed = (
        db.query(func.coalesce(func.sum(Guest.party_size), 0))
        .filter(Guest.allocated_by_guest_id == parent_guest_id)
        .scalar()
        or 0
    )
    if distributed + additional_total > total_allotment:
        remaining = max(total_allotment - distributed, 0)
        raise HTTPException(
            status_code=400,
            detail=f"Only {remaining} ticket(s) remaining — tried to allocate {additional_total}.",
        )