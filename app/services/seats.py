# eventnxt-backend: app/services/seats.py
"""
Assigned seats: generation from section structure, the taken/available
predicate, and the seat-level checkout hold.

Design rules this module enforces:
- Seats are GENERATED, never hand-typed. sync_seats_for_pool makes the
  seats table match the pool's zone_sections exactly, preserving any
  seat that survives the edit (identity = section_label + row_label +
  seat_number) so sold seats keep their tickets through relabeling.
- "Taken" is derived, never stored: a VALID ticket pointing at the seat,
  or a seat hold on a paid / unexpired-pending order. Expiry and refunds
  free seats with zero bookkeeping, exactly like the quantity holds.
- Every mutation that could race (two buyers, one seat) happens under
  SELECT ... FOR UPDATE on the seat rows, ordered by id — the same
  deadlock-safe discipline services/ticketing.py uses on ticket types.
"""

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import tuple_, func, or_
from sqlalchemy.orm import Session

from app.models.order import Order, OrderStatus
from app.models.order_item import OrderItem
from app.models.seat import OrderItemSeat, Seat
from app.models.seating_category import SeatingCategory
from app.models.ticket import Ticket, TicketStatus
from app.models.zone_section import ZoneSection


def _identity(section_label: str, row_label, seat_number: int):
    return (section_label, row_label or None, seat_number)


def taken_seat_ids(db: Session, seat_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """Seats in the list that are NOT free: sold (VALID ticket) or held
    (seat hold on a paid or unexpired pending order)."""
    if not seat_ids:
        return set()
    now = datetime.now(timezone.utc)
    sold = (
        db.query(Ticket.seat_id)
        .filter(Ticket.seat_id.in_(seat_ids), Ticket.status == TicketStatus.VALID)
        .all()
    )
    held = (
        db.query(OrderItemSeat.seat_id)
        .join(OrderItem, OrderItem.id == OrderItemSeat.order_item_id)
        .join(Order, Order.id == OrderItem.order_id)
        .filter(
            OrderItemSeat.seat_id.in_(seat_ids),
            or_(
                Order.status == OrderStatus.PAID,
                (Order.status == OrderStatus.PENDING) & (Order.expires_at > now),
            ),
        )
        .all()
    )
    return {s for (s,) in sold} | {s for (s,) in held}


def block_seats(db: Session, *, category: SeatingCategory, seat_ids: list[uuid.UUID], label) -> list[Seat]:
    """
    Reserve seats (organizer hold). Locks the seat rows FOR UPDATE in id
    order — the same discipline as the buyer-side hold, so an admin
    reserving and a buyer checking out the same seat get exactly one
    winner. Refuses seats that are sold or actively held: a block on a
    seat a buyer already owns would be a lie in the admin view.
    Idempotent over already-blocked seats (relabels them).
    """
    seats = (
        db.query(Seat)
        .filter(Seat.id.in_(seat_ids))
        .order_by(Seat.id)
        .with_for_update()
        .all()
    )
    if len(seats) != len(set(seat_ids)):
        raise HTTPException(status_code=404, detail="One of those seats no longer exists — refresh and try again.")
    for seat in seats:
        if seat.seating_category_id != category.id:
            raise HTTPException(status_code=400, detail=f"{seat.label} isn't in this area.")
    conflicts = taken_seat_ids(db, [s.id for s in seats])
    if conflicts:
        victim = next(s for s in seats if s.id in conflicts)
        raise HTTPException(
            status_code=400,
            detail=f"{victim.label} is already sold or in a buyer's cart — it can't be reserved.",
        )
    clean = (label or "").strip() or None
    for seat in seats:
        seat.is_blocked = True
        seat.block_label = clean
    return seats


def unblock_seats(db: Session, *, category: SeatingCategory, seat_ids: list[uuid.UUID]) -> list[Seat]:
    """Release organizer holds. Locked FOR UPDATE for symmetry; seats
    that weren't blocked pass through untouched (idempotent)."""
    seats = (
        db.query(Seat)
        .filter(Seat.id.in_(seat_ids))
        .order_by(Seat.id)
        .with_for_update()
        .all()
    )
    if len(seats) != len(set(seat_ids)):
        raise HTTPException(status_code=404, detail="One of those seats no longer exists — refresh and try again.")
    for seat in seats:
        if seat.seating_category_id != category.id:
            raise HTTPException(status_code=400, detail=f"{seat.label} isn't in this area.")
        seat.is_blocked = False
        seat.block_label = None
    return seats


def admin_seat_statuses(db: Session, category: SeatingCategory) -> list[dict]:
    """
    Every seat in the pool with its derived status for the organizer's
    seat view. Precedence sold > held > reserved > available (see
    AdminSeatResponse). One taken_seat_ids pass plus one sold query —
    no per-seat queries.
    """
    seats = (
        db.query(Seat)
        .filter(Seat.seating_category_id == category.id)
        .order_by(Seat.section_label, Seat.row_label, Seat.seat_number)
        .all()
    )
    if not seats:
        return []
    ids = [s.id for s in seats]
    # "sold" in the organizer's view means BOX OFFICE — a buyer's ticket.
    # A comp code stamped onto a seat keeps the seat "reserved" (with the
    # guest's name), even though both are VALID tickets to the sale
    # predicates. taken_seat_ids still counts both, so a comp-stamped
    # seat can never sell regardless of its blocked flag.
    box_sold = {
        sid
        for (sid,) in db.query(Ticket.seat_id)
        .filter(Ticket.seat_id.in_(ids), Ticket.status == TicketStatus.VALID, Ticket.guest_id.is_(None))
        .all()
    }
    comp_stamped = {
        sid
        for (sid,) in db.query(Ticket.seat_id)
        .filter(Ticket.seat_id.in_(ids), Ticket.status == TicketStatus.VALID, Ticket.guest_id.isnot(None))
        .all()
    }
    taken = taken_seat_ids(db, ids)
    held = taken - box_sold - comp_stamped
    from app.models.guest import Guest

    guest_ids = {s.guest_id for s in seats if s.guest_id}
    guest_names = (
        {gid: name for (gid, name) in db.query(Guest.id, Guest.name).filter(Guest.id.in_(guest_ids)).all()}
        if guest_ids
        else {}
    )
    out = []
    for s in seats:
        if s.id in box_sold:
            status = "sold"
        elif s.id in held:
            status = "held"
        elif s.is_blocked or s.id in comp_stamped:
            status = "reserved"
        else:
            status = "available"
        out.append(
            {
                "id": s.id,
                "zone_section_id": s.zone_section_id,
                "section_label": s.section_label,
                "row_label": s.row_label,
                "seat_number": s.seat_number,
                "label": s.label,
                "status": status,
                "block_label": s.block_label,
                "guest_id": s.guest_id,
                "guest_name": guest_names.get(s.guest_id),
            }
        )
    return out


def guest_seats(db: Session, guest_id: uuid.UUID) -> list[Seat]:
    return (
        db.query(Seat)
        .filter(Seat.guest_id == guest_id)
        .order_by(Seat.section_label, Seat.row_label, Seat.seat_number)
        .all()
    )


def restamp_guest_tickets(db: Session, guest) -> None:
    """
    Make the guest's VALID comp tickets mirror their assigned seats:
    tickets pointing at a seat no longer theirs are cleared; unstamped
    tickets take assigned seats that no VALID ticket carries yet, in
    seat order. Called after assignment changes AND after minting, so
    assign-then-RSVP and RSVP-then-assign both end in the same place.
    """
    db.flush()  # SessionLocal is autoflush=False — pending guest_id changes must land before we query
    assigned = guest_seats(db, guest.id)
    assigned_ids = {s.id for s in assigned}
    tickets = (
        db.query(Ticket)
        .filter(Ticket.guest_id == guest.id, Ticket.status == TicketStatus.VALID)
        .order_by(Ticket.created_at)
        .all()
    )
    for t in tickets:
        if t.seat_id is not None and t.seat_id not in assigned_ids:
            t.seat_id = None
    carried = {t.seat_id for t in tickets if t.seat_id}
    free_seats = [s for s in assigned if s.id not in carried]
    # Multi-day guard: a seat lives in ONE night's pool, so it stamps
    # onto a code for THAT night. The pool's night is read from any
    # dated ticket type selling it (the fan-out shape); a pool no dated
    # type points at has no night, and falls back to the old rule
    # (undated codes, or the guest's own visit day). Whole-event and
    # per-day-granted comps thus get each seat on the right day's code.
    from app.models.ticket_type import TicketType

    pool_ids = {s.seating_category_id for s in assigned}
    pool_day = {}
    if pool_ids:
        for cat_id, vd in (
            db.query(TicketType.seating_category_id, TicketType.valid_date)
            .filter(TicketType.seating_category_id.in_(pool_ids), TicketType.valid_date.isnot(None))
            .all()
        ):
            pool_day.setdefault(cat_id, vd)

    def stampable(t, seat):
        day = pool_day.get(seat.seating_category_id)
        if day is not None:
            return t.valid_date == day or t.valid_date is None
        return t.valid_date is None or t.valid_date == guest.visit_date

    for seat in list(free_seats):
        target = next((t for t in tickets if t.seat_id is None and stampable(t, seat)), None)
        if target is not None:
            target.seat_id = seat.id
            free_seats.remove(seat)


def assign_guest_seats(db: Session, *, guest, seat_ids: list[uuid.UUID]) -> list[Seat]:
    """
    Wholesale-replace a guest's seat assignment (same full-replace
    contract as sections). Locks every affected seat FOR UPDATE in id
    order — current seats being released plus requested ones — then
    validates UNDER the lock: requested seats must be in the guest's
    pool and neither taken (sold/held) nor another guest's. Assignment
    implies reservation: newly assigned seats become blocked (labeled
    with the guest's name unless already labeled). Released seats KEEP
    their reservation — freeing a press hold is a deliberate act in the
    seat view, never a side effect of reshuffling one guest.
    Finally re-stamps the guest's comp tickets. Caller commits.
    """
    if guest.seating_category_id is None:
        raise HTTPException(
            status_code=400,
            detail=f"{guest.name} isn't assigned to a seating area yet — set their area first, then pick seats.",
        )
    if len(set(seat_ids)) != len(seat_ids):
        raise HTTPException(status_code=400, detail="The same seat was picked twice.")

    current_ids = [s.id for s in guest_seats(db, guest.id)]
    affected = sorted(set(seat_ids) | set(current_ids))
    seats = (
        db.query(Seat).filter(Seat.id.in_(affected)).order_by(Seat.id).with_for_update().all()
        if affected
        else []
    )
    by_id = {s.id: s for s in seats}
    missing = [sid for sid in seat_ids if sid not in by_id]
    if missing:
        raise HTTPException(status_code=404, detail="One of those seats no longer exists — refresh and try again.")

    wanted = [by_id[sid] for sid in seat_ids]
    # Multi-day: a granted guest's seats live in SEVERAL nightly pools
    # (Friday's Row 1, Saturday's Row 1 clone…), so seats may come from
    # any pool of this event — hand-placement is an explicit organizer
    # act, and the conflict/blocked checks below still protect every
    # seat. The guest's own seating_category_id stays their "home" area
    # for capacity math and the UI's seat expander.
    from app.models.seating_category import SeatingCategory as _SC

    pool_events = {
        cid: eid
        for (cid, eid) in db.query(_SC.id, _SC.event_id)
        .filter(_SC.id.in_({s.seating_category_id for s in wanted}))
        .all()
    }
    for seat in wanted:
        if str(pool_events.get(seat.seating_category_id)) != str(guest.event_id):
            raise HTTPException(
                status_code=400,
                detail=f"{seat.label} isn't part of this event's seating.",
            )
        if seat.guest_id is not None and seat.guest_id != guest.id:
            raise HTTPException(status_code=400, detail=f"{seat.label} is already assigned to another guest.")
    conflicts = taken_seat_ids(db, [s.id for s in wanted])
    # A guest's OWN comp tickets make their current seats "taken" — that's
    # not a conflict when re-affirming those seats in a wholesale update.
    own = {
        sid
        for (sid,) in db.query(Ticket.seat_id)
        .filter(
            Ticket.guest_id == guest.id,
            Ticket.status == TicketStatus.VALID,
            Ticket.seat_id.in_([s.id for s in wanted]),
        )
        .all()
    }
    conflicts -= own
    if conflicts:
        victim = next(s for s in wanted if s.id in conflicts)
        raise HTTPException(status_code=400, detail=f"{victim.label} is sold or in a buyer's cart.")

    wanted_ids = set(seat_ids)
    for seat in seats:
        if seat.guest_id == guest.id and seat.id not in wanted_ids:
            seat.guest_id = None  # released from the guest, stays reserved
    for seat in wanted:
        seat.guest_id = guest.id
        if not seat.is_blocked:
            seat.is_blocked = True
        if not seat.block_label:
            seat.block_label = guest.name

    restamp_guest_tickets(db, guest)
    return wanted


def sync_seats_for_pool(db: Session, category: SeatingCategory) -> None:
    """
    Make the seats table match the pool's sections. Called inside the
    same transaction as a sections replace (and on grain changes), AFTER
    the new zone_sections rows exist. Only assigned pools (sales_grain
    'seat') get seats; other grains are left untouched.

    Surviving seats re-link to their new section row; missing seats are
    created; seats that fell off the structure are deleted UNLESS sold
    or actively held — that raises a buyer-readable 400 naming the seat,
    so an organizer can never silently destroy a sold seat.
    """
    if category.sales_grain != "seat":
        return

    sections = (
        db.query(ZoneSection)
        .filter(ZoneSection.seating_category_id == category.id)
        .order_by(ZoneSection.sort_order)
        .all()
    )
    wanted: dict[tuple, ZoneSection | None] = {}
    if sections:
        for sec in sections:
            for n in range(1, sec.capacity + 1):
                wanted[_identity(sec.section_label, sec.row_label, n)] = sec
    else:
        # No section breakdown: the pool itself is one implicit section
        # (pre-composer pools, or a simple assigned block). Labeled by
        # its section_label, else its name — matching migration 0026.
        label = category.section_label or category.name
        for n in range(1, category.capacity + 1):
            wanted[_identity(label, category.row_label, n)] = None

    existing = db.query(Seat).filter(Seat.seating_category_id == category.id).all()
    existing_by_id = {}
    doomed: list[Seat] = []
    for seat in existing:
        key = _identity(seat.section_label, seat.row_label, seat.seat_number)
        if key in wanted:
            sec = wanted[key]
            seat.zone_section_id = sec.id if sec else None  # re-link (replace recreated the rows)
            existing_by_id[key] = seat
        else:
            doomed.append(seat)

    if doomed:
        blocked = taken_seat_ids(db, [s.id for s in doomed])
        # Reserved seats are protected the same way sold ones are: an
        # organizer restructuring sections must release a "Press" hold
        # deliberately, never destroy it as a side effect.
        reserved = [s for s in doomed if s.is_blocked]
        if blocked or reserved:
            victim = next(s for s in doomed if s.id in blocked) if blocked else reserved[0]
            why = "sold or on hold" if blocked else f"reserved ({victim.block_label or 'no label'})"
            raise HTTPException(
                status_code=400,
                detail=f"{victim.label} is {why} — it can't be removed by this structure change.",
            )
        for seat in doomed:
            db.delete(seat)

    for key, sec in wanted.items():
        if key not in existing_by_id:
            section_label, row_label, n = key
            db.add(
                Seat(
                    event_id=category.event_id,
                    seating_category_id=category.id,
                    zone_section_id=sec.id if sec else None,
                    section_label=section_label,
                    row_label=row_label,
                    seat_number=n,
                )
            )


def lock_and_hold_seats(
    db: Session, *, ticket_type, quantity: int, seat_ids: list[uuid.UUID], order_item_id: uuid.UUID
) -> list[Seat]:
    """
    The seat-level hold, called inside create_order_with_hold's
    transaction (ticket-type rows already locked). Locks the requested
    seat rows FOR UPDATE in id order, re-checks conflicts UNDER the
    lock, then writes the hold rows. Any failure raises CheckoutError
    via the caller's contract (we raise ValueError with a buyer-readable
    message; the caller wraps it).
    """
    if len(set(seat_ids)) != len(seat_ids):
        raise ValueError("The same seat was picked twice — each ticket needs its own seat.")
    if len(seat_ids) != quantity:
        raise ValueError(f'"{ticket_type.name}" has {quantity} in your order but {len(seat_ids)} seats picked.')
    if (ticket_type.admits or 1) != 1:
        raise ValueError(f'"{ticket_type.name}" admits {ticket_type.admits} per unit — seat picking applies to single-seat tickets.')

    seats = (
        db.query(Seat)
        .filter(Seat.id.in_(seat_ids))
        .order_by(Seat.id)
        .with_for_update()
        .all()
    )
    if len(seats) != len(seat_ids):
        raise ValueError("One of the picked seats no longer exists — refresh and choose again.")
    for seat in seats:
        if seat.seating_category_id != ticket_type.seating_category_id:
            raise ValueError(f'{seat.label} doesn\'t belong to "{ticket_type.name}".')
        if seat.is_blocked:
            raise ValueError(f"{seat.label} isn't available for sale.")

    conflicts = taken_seat_ids(db, seat_ids)
    if conflicts:
        victim = next(s for s in seats if s.id in conflicts)
        raise ValueError(f"{victim.label} was just taken — pick another seat.")

    for seat in seats:
        db.add(OrderItemSeat(order_item_id=order_item_id, seat_id=seat.id))
    return seats


def lock_and_hold_pass_seats(
    db: Session, *, pass_type, members, quantity: int, seat_ids: list[uuid.UUID], order_item_id: uuid.UUID
) -> list[Seat]:
    """
    The all-days pass hold: the buyer picked seats from the FIRST
    member's pool (that's the map the picker serves); each picked seat
    is a seat IDENTITY — (section, row, number) — that must be claimed
    in EVERY member pool. All sibling seat rows across all nights are
    locked FOR UPDATE in one id-ordered set (the same global discipline
    as every other hold, so a pass buyer racing a single-night buyer for
    the same chair gets exactly one winner), checked free and unblocked
    under the lock, then held on the one pass order item. Fulfillment
    mints one dated code per held seat.
    """
    if len(set(seat_ids)) != len(seat_ids):
        raise ValueError("The same seat was picked twice — each pass needs its own seat.")
    if len(seat_ids) != quantity:
        raise ValueError(f'"{pass_type.name}" has {quantity} in your order but {len(seat_ids)} seats picked.')

    pool_ids = [m.seating_category_id for m in members]
    base_seats = db.query(Seat).filter(Seat.id.in_(seat_ids)).all()
    if len(base_seats) != len(seat_ids):
        raise ValueError("One of the picked seats no longer exists — refresh and choose again.")
    for s in base_seats:
        if s.seating_category_id != pool_ids[0]:
            raise ValueError(f'{s.label} doesn\'t belong to "{pass_type.name}".')

    identities = [(s.section_label, s.row_label, s.seat_number) for s in base_seats]
    all_seats = (
        db.query(Seat)
        .filter(
            Seat.seating_category_id.in_(pool_ids),
            tuple_(Seat.section_label, Seat.row_label, Seat.seat_number).in_(identities),
        )
        .order_by(Seat.id)
        .with_for_update()
        .all()
    )
    # Every identity must exist on every night — layouts that diverged
    # after the pass was created narrow what the pass can sell.
    by_pool: dict = {}
    for s in all_seats:
        by_pool.setdefault(s.seating_category_id, []).append(s)
    for m in members:
        found = by_pool.get(m.seating_category_id, [])
        if len(found) != len(identities):
            raise ValueError(
                f'One of those seats doesn\'t exist on every night of "{pass_type.name}" — the layouts differ. Pick another seat.'
            )
        for s in found:
            if s.is_blocked:
                raise ValueError(f"{s.label} isn't available for sale on one of the nights.")

    conflicts = taken_seat_ids(db, [s.id for s in all_seats])
    if conflicts:
        victim = next(s for s in all_seats if s.id in conflicts)
        raise ValueError(f"{victim.label} was just taken on one of the nights — pick another seat.")

    for s in all_seats:
        db.add(OrderItemSeat(order_item_id=order_item_id, seat_id=s.id))
    return all_seats


def seats_for_order_item(db: Session, order_item_id: uuid.UUID) -> list[Seat]:
    return (
        db.query(Seat)
        .join(OrderItemSeat, OrderItemSeat.seat_id == Seat.id)
        .filter(OrderItemSeat.order_item_id == order_item_id)
        .order_by(Seat.section_label, Seat.seat_number)
        .all()
    )


# ---------- Section-level selling (unassigned rows / tables) ----------


def section_required_pool_ids(db: Session, pool_ids: list) -> set:
    """Pools whose ticket types must carry a section at checkout: they
    have a section breakdown and are NOT seat-assigned (seat picking
    covers those)."""
    if not pool_ids:
        return set()
    pools = db.query(SeatingCategory).filter(SeatingCategory.id.in_(pool_ids)).all()
    with_sections = {
        sid for (sid,) in db.query(ZoneSection.seating_category_id).filter(ZoneSection.seating_category_id.in_(pool_ids)).distinct()
    }
    return {p.id for p in pools if p.sales_grain in ("row", "table") and p.id in with_sections}


def section_heads_taken(db: Session, zone_section_id) -> int:
    """Heads committed to a section by box office: paid or unexpired
    pending order items × the ticket type's admits — PLUS row/GA pass
    claims on this section for their night (order_item_pass_sections;
    passes are admits-1, so heads = item quantity). Comps float at pool
    level (they have no section) and are governed by pool capacity."""
    from app.models.order_item import OrderItemPassSection
    from app.models.ticket_type import TicketType

    now = datetime.now(timezone.utc)
    direct = int(
        db.query(func.coalesce(func.sum(OrderItem.quantity * TicketType.admits), 0))
        .join(Order, Order.id == OrderItem.order_id)
        .join(TicketType, TicketType.id == OrderItem.ticket_type_id)
        .filter(
            OrderItem.zone_section_id == zone_section_id,
            or_(
                Order.status == OrderStatus.PAID,
                (Order.status == OrderStatus.PENDING) & (Order.expires_at > now),
            ),
        )
        .scalar()
        or 0
    )
    pass_claims = int(
        db.query(func.coalesce(func.sum(OrderItem.quantity), 0))
        .join(OrderItemPassSection, OrderItemPassSection.order_item_id == OrderItem.id)
        .join(Order, Order.id == OrderItem.order_id)
        .filter(
            OrderItemPassSection.zone_section_id == zone_section_id,
            or_(
                Order.status == OrderStatus.PAID,
                (Order.status == OrderStatus.PENDING) & (Order.expires_at > now),
            ),
        )
        .scalar()
        or 0
    )
    return direct + pass_claims


def lock_and_claim_section(db: Session, *, ticket_type, quantity: int, zone_section_id, order_item) -> None:
    """
    The section-level hold: lock the section row FOR UPDATE (always
    after ticket-type locks — consistent global order, same deadlock
    discipline as seats), re-check heads under the lock, stamp the
    order item. Raises ValueError with a buyer-readable message.
    """
    section = (
        db.query(ZoneSection).filter(ZoneSection.id == zone_section_id).with_for_update().first()
    )
    if not section or section.seating_category_id != ticket_type.seating_category_id:
        raise ValueError(f'That section doesn\'t belong to "{ticket_type.name}" — refresh and choose again.')
    heads_wanted = quantity * (ticket_type.admits or 1)
    heads_taken = section_heads_taken(db, section.id)
    # Comp holds: confirmed guests and pending hold-now guests protect
    # their heads in this section — buyers see only what's genuinely
    # left. Day-aware via the pool's own day (see seating.guest_hold_heads).
    from app.models.seating_category import SeatingCategory as _SC
    from app.services import seating as seating_service

    _cat = db.query(_SC).filter(_SC.id == section.seating_category_id).first()
    held_by_guests = seating_service.guest_hold_heads(db, _cat, section.section_label) if _cat else 0
    if heads_taken + held_by_guests + heads_wanted > section.capacity:
        left = max(section.capacity - heads_taken - held_by_guests, 0)
        raise ValueError(
            f"Section {section.section_label} only has {left} left — someone may have beaten you to it. "
            "Pick another section or adjust the quantity."
        )
    order_item.zone_section_id = section.id
    order_item.section_label = (
        f"Section {section.section_label}" + (f" · {section.row_label}" if section.row_label else "")
    )

def lock_and_claim_pass_sections(
    db: Session, *, pass_type, members, quantity: int, zone_section_ids: list, order_item
) -> None:
    """
    The row/GA-pass mirror of lock_and_hold_pass_seats: the buyer chose
    one section PER NIGHT (they may differ — different views of the
    show), and every unit on the item follows those picks. Lock the
    chosen section rows FOR UPDATE in id order (after ticket-type
    locks — same global discipline), re-check each night's heads under
    the lock (box office + other pass claims + comp holds), then write
    one claim row per night. Raises ValueError with a buyer-readable
    message; heads free automatically on expiry/refund by not counting.
    """
    from app.models.order_item import OrderItemPassSection
    from app.models.seating_category import SeatingCategory as _SC
    from app.services import seating as seating_service

    member_pool_ids = {m.seating_category_id for m in members if m.seating_category_id}
    picked = [x for x in (zone_section_ids or []) if x]
    if len(picked) != len(members):
        raise ValueError(f'"{pass_type.name}" needs a section pick for each of its {len(members)} nights.')
    sections = (
        db.query(ZoneSection)
        .filter(ZoneSection.id.in_(picked))
        .order_by(ZoneSection.id)
        .with_for_update()
        .all()
    )
    if len(sections) != len(set(picked)) or len(set(picked)) != len(picked):
        raise ValueError("One of the chosen sections no longer exists — refresh and choose again.")
    pools_hit = [s.seating_category_id for s in sections]
    if set(pools_hit) - member_pool_ids or len(set(pools_hit)) != len(members):
        raise ValueError(f'"{pass_type.name}" needs exactly one section per night — refresh and choose again.')

    pools = {c.id: c for c in db.query(_SC).filter(_SC.id.in_(pools_hit)).all()}
    day_by_pool = {m.seating_category_id: m.valid_date for m in members}
    labels = []
    for section in sorted(sections, key=lambda s: day_by_pool.get(s.seating_category_id) or ""):
        heads_taken = section_heads_taken(db, section.id)
        cat = pools.get(section.seating_category_id)
        held_by_guests = seating_service.guest_hold_heads(db, cat, section.section_label) if cat else 0
        if heads_taken + held_by_guests + quantity > section.capacity:
            left = max(section.capacity - heads_taken - held_by_guests, 0)
            day = day_by_pool.get(section.seating_category_id) or ""
            raise ValueError(
                f"Section {section.section_label} on {day} only has {left} left — someone may have "
                "beaten you to it. Pick another section for that night or adjust the quantity."
            )
        db.add(OrderItemPassSection(order_item_id=order_item.id, zone_section_id=section.id))
        day = day_by_pool.get(section.seating_category_id)
        labels.append((f"{day} — " if day else "") + f"Section {section.section_label}")
    # One snapshot the order page and emails can show as-is.
    order_item.section_label = " · ".join(labels)
    db.flush()