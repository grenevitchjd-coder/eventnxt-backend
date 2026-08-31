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
from sqlalchemy import func, or_
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
        if blocked:
            victim = next(s for s in doomed if s.id in blocked)
            raise HTTPException(
                status_code=400,
                detail=f"{victim.label} is sold or on hold — it can't be removed by this structure change.",
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
    pending order items × the ticket type's admits. Comps float at pool
    level (they have no section) and are governed by pool capacity."""
    from app.models.ticket_type import TicketType

    now = datetime.now(timezone.utc)
    return int(
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
    if heads_taken + heads_wanted > section.capacity:
        left = max(section.capacity - heads_taken, 0)
        raise ValueError(
            f"Section {section.section_label} only has {left} left — someone may have beaten you to it. "
            "Pick another section or adjust the quantity."
        )
    order_item.zone_section_id = section.id
    order_item.section_label = (
        f"Section {section.section_label}" + (f" · {section.row_label}" if section.row_label else "")
    )