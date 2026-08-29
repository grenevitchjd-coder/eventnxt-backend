from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.guest import Guest, GuestAllocationStatus
from app.models.guest_ticket_allotment import GuestTicketAllotment
from app.models.guest_type import GuestType
from app.models.guest_type_seating_priority import GuestTypeSeatingPriority
from app.models.guest_type_ticket_allotment import GuestTypeTicketAllotment
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


def effective_allotment(db: Session, guest: Guest) -> dict:
    """
    A guest's per-day ticket allotment — {date: quantity}. Uses the
    guest's own override rows if ticket_allotment_overridden is set,
    otherwise inherits the guest type's default rows.

    A guest created via someone else's distribution
    (allocated_by_guest_id set) ALWAYS gets {} here, regardless of the
    override flag or their type's default — distribution is one level
    deep on purpose. Without this check, a delegated recipient sharing a
    guest type with their distributor (the common case — see the
    distribute endpoint) would incorrectly inherit the type's default
    allotment and look like a distributor themselves.
    """
    if guest.allocated_by_guest_id is not None:
        return {}

    if guest.ticket_allotment_overridden:
        rows = db.query(GuestTicketAllotment).filter(GuestTicketAllotment.guest_id == guest.id).all()
    else:
        rows = (
            db.query(GuestTypeTicketAllotment)
            .filter(GuestTypeTicketAllotment.guest_type_id == guest.guest_type_id)
            .all()
        )
    return {r.date: r.quantity for r in rows}


def is_allotment_holder(allotment: dict) -> bool:
    return any(q > 0 for q in allotment.values())


def replace_guest_ticket_allotment(db: Session, guest_id: str, items) -> None:
    """
    Wholesale replace a guest's own per-day override rows with `items`
    (a list of objects with .date and .quantity — accepts the Pydantic
    schema items directly). Does NOT touch ticket_allotment_overridden;
    the caller sets that.
    """
    db.query(GuestTicketAllotment).filter(GuestTicketAllotment.guest_id == guest_id).delete()
    for item in items:
        db.add(GuestTicketAllotment(guest_id=guest_id, date=item.date, quantity=item.quantity))


def check_allotment_capacity_per_day(
    db: Session, parent_guest_id: str, requested_by_day: dict, allotment: dict
):
    """
    Row-level lock on the PARENT guest (the model/sponsor distributing
    tickets) — holds it for the rest of this transaction so two
    simultaneous distribution submissions from the same person can't both
    slip past the remaining-tickets check. Only ever locks one row per
    transaction (the parent), so unlike seating there's no multi-row
    lock-ordering to worry about — different parents' distributions never
    contend with each other.

    Checked PER DAY, not as one combined total — "10 Thursday, 5
    Saturday" are separate pools, so using up all 10 Thursday tickets
    must never block someone from getting a Saturday ticket, and vice
    versa.
    """
    db.query(Guest).filter(Guest.id == parent_guest_id).with_for_update().first()

    children = db.query(Guest).filter(Guest.allocated_by_guest_id == parent_guest_id).all()
    distributed_by_day = {}
    for c in children:
        if c.visit_date:
            distributed_by_day[c.visit_date] = distributed_by_day.get(c.visit_date, 0) + c.party_size

    for date, requested_qty in requested_by_day.items():
        total_for_day = allotment.get(date, 0)
        already = distributed_by_day.get(date, 0)
        if already + requested_qty > total_for_day:
            remaining = max(total_for_day - already, 0)
            raise HTTPException(
                status_code=400,
                detail=f"Only {remaining} ticket(s) remaining for {date} — tried to allocate {requested_qty}.",
            )