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


def pool_for_day(db: Session, base_pool_id, visit_date):
    """
    Multi-day events clone a pool per day ("Row 2", "Row 2 (10/10)", …)
    with each day's ticket type pointing at its own clone. Priorities are
    configured ONCE against any night's pool; this maps that pool to the
    sibling serving the guest's day: base pool -> its dated ticket type
    -> the same-named family member for visit_date -> that member's
    pool. Falls back to the base pool when there's nothing to map (no
    visit date, undated types, or no sibling for that day).
    """
    if not visit_date:
        return base_pool_id
    from app.models.ticket_type import TicketType

    base_type = (
        db.query(TicketType)
        .filter(TicketType.seating_category_id == base_pool_id, TicketType.valid_date.isnot(None))
        .first()
    )
    if not base_type:
        return base_pool_id
    if base_type.valid_date == visit_date:
        return base_pool_id
    sibling = (
        db.query(TicketType)
        .filter(
            TicketType.event_id == base_type.event_id,
            TicketType.name == base_type.name,
            TicketType.valid_date == visit_date,
            TicketType.seating_category_id.isnot(None),
        )
        .first()
    )
    return sibling.seating_category_id if sibling else base_pool_id


def resolve_seating_from_priorities(db: Session, event_id: str, guest_type_id: str, party_size: int = 1, visit_date=None):
    """Pool-only view of resolve_seating_placement, kept for call sites
    that only store a category (redemptions, request approval)."""
    category_id, _section = resolve_seating_placement(
        db, event_id, guest_type_id, party_size=party_size, visit_date=visit_date
    )
    return category_id


def section_room_for_comps(db: Session, category: SeatingCategory, section_label: str, exclude_guest_id=None) -> int:
    """
    How many more comp heads fit in one section of a pool. The section's
    capacity minus box-office heads (paid/pending-unexpired) minus comp
    heads already placed in this section. For assigned-seat pools the
    box-office+reserved side is counted through the seats themselves
    (free, unblocked seats), minus seatless comp heads placed here —
    guests holding actual seats consume via their blocked seats, so
    they're never double-counted.

    Matching is by LABEL (all sections of the pool sharing the label sum
    together); a label that no longer exists yields 0 room and the
    resolver falls through to the next priority.
    """
    from app.models.zone_section import ZoneSection
    from app.services import seats as seats_service

    sections = (
        db.query(ZoneSection)
        .filter(ZoneSection.seating_category_id == category.id, ZoneSection.section_label == section_label)
        .all()
    )
    if not sections:
        return 0

    comp_q = db.query(func.coalesce(func.sum(Guest.party_size), 0)).filter(
        Guest.seating_category_id == category.id,
        Guest.section_label == section_label,
        Guest.allocation_status == GuestAllocationStatus.CONFIRMED,
    )
    if exclude_guest_id:
        comp_q = comp_q.filter(Guest.id != exclude_guest_id)
    comp_heads_here = comp_q.scalar() or 0

    if category.sales_grain == "seat":
        from app.models.seat import Seat

        seats = (
            db.query(Seat)
            .filter(Seat.seating_category_id == category.id, Seat.section_label == section_label)
            .all()
        )
        if not seats:
            return 0
        taken = seats_service.taken_seat_ids(db, [s.id for s in seats])
        free = sum(1 for s in seats if s.id not in taken and not s.is_blocked)
        # Seat-holding guests are inside `blocked`; only seatless comps
        # placed in this section still draw from the free count.
        seated_guest_ids = {s.guest_id for s in seats if s.guest_id}
        seatless_q = db.query(func.coalesce(func.sum(Guest.party_size), 0)).filter(
            Guest.seating_category_id == category.id,
            Guest.section_label == section_label,
            Guest.allocation_status == GuestAllocationStatus.CONFIRMED,
        )
        if seated_guest_ids:
            seatless_q = seatless_q.filter(~Guest.id.in_(seated_guest_ids))
        if exclude_guest_id:
            seatless_q = seatless_q.filter(Guest.id != exclude_guest_id)
        seatless_comp_heads = seatless_q.scalar() or 0
        return max(free - seatless_comp_heads, 0)

    capacity = sum(sec.capacity for sec in sections)
    box_office = sum(seats_service.section_heads_taken(db, sec.id) for sec in sections)
    return max(capacity - box_office - comp_heads_here, 0)


def resolve_seating_placement(db: Session, event_id: str, guest_type_id: str, party_size: int = 1, visit_date=None):
    """
    Walks the guest type's ORDERED priority list and returns
    (category_id, section_label) for the first entry with room for
    `party_size`, or (None, None). section_label is None when a
    pool-level entry wins — the guest floats at pool level exactly as
    before Slice C.

    Deadlock safety: a guest creation may need to lock several categories
    in one transaction. If we locked them in PRIORITY order, two
    concurrent creations with overlapping priority lists in different
    orders could deadlock (transaction A holds category X waiting on Y,
    transaction B holds Y waiting on X — a circular wait). Instead every
    transaction locks the same set of candidate categories in a fixed,
    deterministic order (by id) regardless of priority — since every
    transaction acquires locks in that identical sequence, a circular
    wait, and therefore a deadlock, can never form. The actual business
    decision is made afterward, using the already-locked data, walking
    the REAL priority order. Section-level entries are guarded by the
    same pool lock: every comp-placement path locks the pool row, so two
    comps can't both take a section's last head. (A comp racing a BUYER
    for the last head of a section is the same estimated-vs-confirmed
    exposure comps have always had at pool level — the reconciliation
    summary is the source of truth there.)
    """
    priorities = (
        db.query(GuestTypeSeatingPriority)
        .filter(GuestTypeSeatingPriority.guest_type_id == guest_type_id)
        .order_by(GuestTypeSeatingPriority.priority_order)
        .all()
    )
    if not priorities:
        return None, None

    # Day-aware substitution: a visit-dated guest resolves against the
    # pools serving THEIR day (per-day clones), configured once against
    # any night. Substitution happens before locking so the lock set is
    # the set actually judged.
    effective = {p.id: pool_for_day(db, p.seating_category_id, visit_date) for p in priorities}
    category_ids = list(set(effective.values()))

    locked_categories = (
        db.query(SeatingCategory)
        .filter(SeatingCategory.id.in_(category_ids), SeatingCategory.event_id == event_id)
        .order_by(SeatingCategory.id)  # fixed lock order — NOT priority order
        .with_for_update()
        .all()
    )
    categories_by_id = {c.id: c for c in locked_categories}

    for p in priorities:  # now walk the REAL priority order to decide
        category = categories_by_id.get(effective[p.id])
        if not category:
            continue
        if p.section_label:
            if section_room_for_comps(db, category, p.section_label) >= party_size:
                return category.id, p.section_label
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
            return category.id, None

    return None, None


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


def allotment_summary(db: Session, guest: Guest):
    """
    (total, distributed) across ALL days of a guest's allotment — for
    surfacing "12 of 20 tickets given out" in the organizer's guest list,
    without needing a separate call per guest. (0, 0) for a guest with no
    allotment at all. Sums every distributed child's party_size
    regardless of which specific day they're for, matching how the RSVP
    page's per-day breakdown adds up in total — this is the same
    aggregate, just not split by day, since the organizer list is a
    glance-at-a-row view rather than a day-by-day planning view.
    """
    allotment = effective_allotment(db, guest)
    total = sum(allotment.values())
    if total == 0:
        return 0, 0
    distributed = (
        db.query(func.coalesce(func.sum(Guest.party_size), 0))
        .filter(Guest.allocated_by_guest_id == guest.id)
        .scalar()
        or 0
    )
    return total, distributed


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

def check_section_capacity(
    db: Session, event_id: str, category_id: str, section_label: str, party_size: int = 1, exclude_guest_id=None
):
    """
    Organizer explicitly placing a confirmed guest into a section: lock
    the pool row (same lock every comp path takes), verify the label
    exists, verify the section has room. Called AFTER check_capacity in
    the routers, so the pool row is already locked in this transaction —
    the extra with_for_update here is a no-op re-acquire, kept so this
    function is safe to call alone too.
    """
    category = (
        db.query(SeatingCategory)
        .filter(SeatingCategory.id == category_id, SeatingCategory.event_id == event_id)
        .with_for_update()
        .first()
    )
    if not category:
        raise HTTPException(status_code=404, detail="Seating category not found for this event.")
    from app.models.zone_section import ZoneSection

    exists = (
        db.query(ZoneSection.id)
        .filter(ZoneSection.seating_category_id == category.id, ZoneSection.section_label == section_label)
        .first()
    )
    if not exists:
        raise HTTPException(
            status_code=400,
            detail=f'"{category.name}" has no section "{section_label}" — pick one of its sections or leave section blank.',
        )
    room = section_room_for_comps(db, category, section_label, exclude_guest_id=exclude_guest_id)
    if room < party_size:
        raise HTTPException(
            status_code=400,
            detail=f'Section {section_label} of "{category.name}" only has {room} seat(s) left — not enough for {party_size}.',
        )