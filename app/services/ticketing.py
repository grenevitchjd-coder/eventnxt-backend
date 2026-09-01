"""
eventnxt-backend: app/services/ticketing.py

The inventory-and-money core of native ticket sales.

Concurrency contract (the house rule: row lock + real test, never "looks
correct"): every path that consumes inventory locks the ticket_type rows
FOR UPDATE, in a consistent order (by id — same deadlock-safe discipline
as seating). Availability = quantity − sold − held, where sold is tickets
on PAID orders and held is tickets on PENDING orders whose expires_at is
still in the future. An abandoned checkout releases itself by pure
passage of time — no cleanup job.

Money contract: integer cents everywhere; the platform fee is computed
from TODAY's configured rate and SNAPSHOTTED onto the order — later
repricing never rewrites history. $0 orders pay no fee and never touch
Stripe.
"""

import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func as safunc
from sqlalchemy.orm import Session

from app.config import settings
from app.models.order import Order, OrderStatus
from app.models.order_item import OrderItem
from app.models.ticket import Ticket, TicketStatus
from app.models.ticket_type import TicketType
from app.services import seats as seats_service
from app.services.email import EmailNotConfigured, EmailSendError, send_email

PENDING_HOLD_MINUTES = 30  # matched to the Stripe Checkout session expiry


class CheckoutError(Exception):
    """A buyer-facing problem with the requested purchase (sold out, over limit, etc.)."""


def generate_order_token() -> str:
    return secrets.token_urlsafe(24)


def generate_ticket_code() -> str:
    # Human-typeable at the door: T + 10 hex chars, uppercase. Collision
    # odds are negligible and the unique index is the final referee.
    return "T" + secrets.token_hex(5).upper()


def compute_platform_fee_cents(subtotal_cents: int) -> int:
    """3% + 75¢ at current config — $0 orders carry no fee (comps are free in every sense)."""
    if subtotal_cents <= 0:
        return 0
    return round(subtotal_cents * settings.platform_fee_percent / 100) + settings.platform_fee_fixed_cents


def compute_discount_cents(promo_code, subtotal_cents: int) -> int:
    """
    The buyer discount a code takes off a face-value subtotal. Zero when
    the code carries no discount terms (attribution-only codes). Clamped
    to the subtotal — a code can make an order free, never negative.
    """
    if promo_code is None or promo_code.discount_type is None or promo_code.discount_value is None:
        return 0
    if promo_code.discount_type == "percentage":
        raw = round(subtotal_cents * float(promo_code.discount_value) / 100)
    else:  # flat_amount — stored in dollars, converted at this boundary
        raw = round(float(promo_code.discount_value) * 100)
    return max(0, min(subtotal_cents, raw))


def committed_quantities(db: Session, ticket_type_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
    """
    Per ticket type: {'sold': N, 'held': M}. Sold = PAID orders; held =
    PENDING orders whose hold hasn't expired. EXPIRED/REFUNDED count as
    nothing — refunded inventory goes back on sale by simply not counting.
    """
    now = datetime.now(timezone.utc)
    rows = (
        db.query(
            OrderItem.ticket_type_id,
            Order.status,
            safunc.sum(OrderItem.quantity),
        )
        .join(Order, OrderItem.order_id == Order.id)
        .filter(
            OrderItem.ticket_type_id.in_(ticket_type_ids),
            (
                (Order.status == OrderStatus.PAID)
                | ((Order.status == OrderStatus.PENDING) & (Order.expires_at > now))
            ),
        )
        .group_by(OrderItem.ticket_type_id, Order.status)
        .all()
    )
    result: dict[uuid.UUID, dict] = {tid: {"sold": 0, "held": 0} for tid in ticket_type_ids}
    for tid, status, qty in rows:
        if status == OrderStatus.PAID:
            result[tid]["sold"] += int(qty)
        else:
            result[tid]["held"] += int(qty)
    return result


def availability_for(db: Session, ticket_types: list[TicketType]) -> dict[uuid.UUID, dict]:
    counts = committed_quantities(db, [t.id for t in ticket_types])
    out = {}
    for t in ticket_types:
        c = counts[t.id]
        out[t.id] = {
            "sold": c["sold"],
            "held": c["held"],
            "available": max(0, t.quantity - c["sold"] - c["held"]),
        }
    return out


def is_on_sale(t: TicketType, available: int) -> bool:
    now = datetime.now(timezone.utc)
    if not t.is_active or available <= 0:
        return False
    if t.sales_start and now < t.sales_start:
        return False
    if t.sales_end and now > t.sales_end:
        return False
    return True


def create_pending_order(
    db: Session,
    event_id: uuid.UUID,
    buyer_name: str,
    buyer_email: str,
    requested: list[tuple[uuid.UUID, int, list[uuid.UUID] | None, uuid.UUID | None]],  # (ticket_type_id, quantity, seat_ids, zone_section_id)
    promo_code=None,  # a resolved PromoCode (or None) — discount + attribution applied here, atomically
) -> Order:
    """
    The critical section. Locks the ticket_type rows (ordered by id —
    deadlock-safe), re-checks availability UNDER the lock, then creates
    the order + items + hold. Raises CheckoutError with a buyer-readable
    message on any problem. Caller commits.
    """
    ids = sorted({tid for tid, _, _, _ in requested})
    qty_by_id: dict[uuid.UUID, int] = {}
    seats_by_id: dict[uuid.UUID, list[uuid.UUID]] = {}
    section_by_id: dict[uuid.UUID, uuid.UUID | None] = {}
    for tid, qty, seat_ids, zone_section_id in requested:
        qty_by_id[tid] = qty_by_id.get(tid, 0) + qty
        if seat_ids:
            seats_by_id.setdefault(tid, []).extend(seat_ids)
        if zone_section_id:
            section_by_id[tid] = zone_section_id

    ticket_types = (
        db.query(TicketType)
        .filter(TicketType.id.in_(ids), TicketType.event_id == event_id)
        .order_by(TicketType.id)
        .with_for_update()
        .all()
    )
    if len(ticket_types) != len(ids):
        raise CheckoutError("One of the selected ticket types no longer exists for this event.")

    avail = availability_for(db, ticket_types)

    for t in ticket_types:
        want = qty_by_id[t.id]
        a = avail[t.id]["available"]
        if not is_on_sale(t, a):
            raise CheckoutError(f'"{t.name}" is not currently on sale.')
        if want > t.max_per_order:
            raise CheckoutError(f'"{t.name}" allows at most {t.max_per_order} per order.')
        if want > a:
            raise CheckoutError(
                f'Only {a} left for "{t.name}" — someone may have beaten you to it. '
                "Adjust the quantity and try again."
            )

    subtotal = sum(t.price_cents * qty_by_id[t.id] for t in ticket_types)
    discount = compute_discount_cents(promo_code, subtotal)
    charged = subtotal - discount
    # The fee is computed on money that actually moves — a 100%-off code
    # produces a $0 charge and a $0 fee, same as a free ticket type.
    fee = compute_platform_fee_cents(charged)

    order = Order(
        event_id=event_id,
        organization_id=ticket_types[0].organization_id,
        status=OrderStatus.PENDING,
        buyer_name=buyer_name.strip(),
        buyer_email=buyer_email.strip().lower(),  # lowercased: this is the "find my tickets" key
        currency=ticket_types[0].currency,
        subtotal_cents=subtotal,
        discount_cents=discount,
        platform_fee_cents=fee,
        organizer_net_cents=charged - fee,
        promo_code_id=promo_code.id if promo_code else None,
        order_token=generate_order_token(),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=PENDING_HOLD_MINUTES),
    )
    db.add(order)
    # autoflush is OFF in this app (deliberate, existing choice) — flush
    # explicitly so order.id exists for the items. This is the exact
    # pattern the bonus-tier bug taught (handoff doc, Section 5).
    db.flush()

    order_items: dict[uuid.UUID, OrderItem] = {}
    for t in ticket_types:
        item = OrderItem(
            order_id=order.id,
            ticket_type_id=t.id,
            quantity=qty_by_id[t.id],
            unit_price_cents=t.price_cents,  # snapshot
            ticket_type_name=t.name,  # snapshot
        )
        db.add(item)
        order_items[t.id] = item
    db.flush()

    # Section requirement: sectioned unassigned types must carry a
    # section; the claim locks the section row and enforces its own
    # capacity (heads) — the section-level mirror of the seat holds.
    required = seats_service.section_required_pool_ids(
        db, [t.seating_category_id for t in ticket_types if t.seating_category_id]
    )
    for t in ticket_types:
        if t.seating_category_id in required:
            picked_section = section_by_id.get(t.id)
            if not picked_section:
                raise CheckoutError(f'"{t.name}" is sold by section — pick which section you want.')
            try:
                seats_service.lock_and_claim_section(
                    db, ticket_type=t, quantity=qty_by_id[t.id], zone_section_id=picked_section, order_item=order_items[t.id]
                )
            except ValueError as exc:
                raise CheckoutError(str(exc))

    # Seat-level holds — the assigned-seat mirror of the quantity hold
    # above, inside the same locked transaction. Conflicts surface as
    # buyer-readable CheckoutErrors.
    for t in ticket_types:
        picked = seats_by_id.get(t.id)
        if not picked:
            continue
        try:
            seats_service.lock_and_hold_seats(
                db, ticket_type=t, quantity=qty_by_id[t.id], seat_ids=picked, order_item_id=order_items[t.id].id
            )
        except ValueError as exc:
            raise CheckoutError(str(exc))
    db.flush()
    return order


def fulfill_paid_order(db: Session, order: Order) -> list[Ticket]:
    """
    Marks the order PAID and mints its Ticket rows (one per admission,
    unique code each). Idempotent by construction: called only from the
    webhook (which is idempotency-guarded) or the $0 instant path, and
    a second call on an already-PAID order returns the existing tickets
    instead of minting again. Caller commits.
    """
    if order.status == OrderStatus.PAID:
        return db.query(Ticket).filter(Ticket.order_id == order.id).all()

    order.status = OrderStatus.PAID
    order.paid_at = datetime.now(timezone.utc)
    order.expires_at = None  # paid orders hold forever; the deadline is meaningless now
    db.flush()

    items = db.query(OrderItem).filter(OrderItem.order_id == order.id).all()
    # One purchased unit mints `admits` codes (whole tables, group packs).
    tts_by_id = {
        tt.id: tt
        for tt in db.query(TicketType).filter(TicketType.id.in_([i.ticket_type_id for i in items])).all()
    } if items else {}
    # Multi-day: a whole-event admission fans out to one dated code per
    # event day; a dated type mints for its day only; a single-day event
    # (no day list) mints undated codes exactly as before 0032.
    from app.services.comp_tickets import event_days_for

    event_days = event_days_for(db, order.event_id)
    tickets: list[Ticket] = []
    for item in items:
        tt = tts_by_id.get(item.ticket_type_id)
        if tt is not None and tt.valid_date:
            dates = [tt.valid_date]
        elif event_days:
            dates = event_days
        else:
            dates = [None]
        # Assigned seats: stamp each minted code with its chosen seat.
        # Seat-picked items are admits-1 (enforced at hold time), so the
        # ADMISSION count equals the seat count; each admission then
        # mints one code per date — a whole-event seat purchase keeps
        # the same seat on every day's code.
        seat_iter = iter(seats_service.seats_for_order_item(db, item.id))
        for _ in range(item.quantity * ((tt.admits if tt else 1) or 1)):
            seat = next(seat_iter, None)
            for valid_date in dates:
                ticket = Ticket(
                    order_id=order.id,
                    order_item_id=item.id,
                    ticket_type_id=item.ticket_type_id,
                    event_id=order.event_id,
                    code=generate_ticket_code(),
                    status=TicketStatus.VALID,
                    seat_id=seat.id if seat else None,
                    valid_date=valid_date,
                )
                db.add(ticket)
                tickets.append(ticket)
    db.flush()
    return tickets


def _format_money(cents: int, currency: str) -> str:
    symbol = "$" if currency.lower() == "usd" else ""
    return f"{symbol}{cents / 100:.2f} {currency.upper()}" if not symbol else f"{symbol}{cents / 100:.2f}"


def send_order_confirmation_email(order: Order, tickets: list[Ticket], event_title: str, order_url: str) -> bool:
    """
    Best-effort by design: the paid order is sacred, the email is
    retryable (the buyer can always self-serve via Find My Tickets).
    Returns True if sent; never raises.
    """
    try:
        def _day(t):
            if not t.valid_date:
                return ""
            from datetime import date as _date

            try:
                return f" [{_date.fromisoformat(t.valid_date).strftime('%a %b %-d')}]"
            except ValueError:
                return f" [{t.valid_date}]"

        ticket_lines = "\n".join(f"  - {t.code}{_day(t)}" for t in tickets)
        qr_base = f"{settings.eventnxt_backend_url}/public/tickets"
        ticket_rows = "".join(
            f"<tr><td style='padding:10px 12px;text-align:center'>"
            f"<img src='{qr_base}/{t.code}/qr.png' width='150' height='150' "
            f"style='display:block;margin:0 auto 6px;border-radius:8px' alt='QR for {t.code}'/>"
            f"<span style='font-family:monospace;font-size:15px'>{t.code}</span>"
            + (f"<br/><span style='font-size:13px;font-weight:600'>{_day(t).strip()}</span>" if t.valid_date else "")
            + f"</td></tr>"
            for t in tickets
        )
        total = _format_money(order.subtotal_cents, order.currency)
        send_email(
            to=order.buyer_email,
            subject=f"Your tickets for {event_title}",
            text_body=(
                f"Hi {order.buyer_name},\n\n"
                f"You're in! Here are your tickets for {event_title}.\n\n"
                f"Total paid: {total}\n\n"
                f"Ticket codes:\n{ticket_lines}\n\n"
                f"View your order any time:\n{order_url}\n\n"
                "Keep this email — your codes are your admission.\n\n"
                "— EventNXT"
            ),
            html_body=(
                f"<p>Hi {order.buyer_name},</p>"
                f"<p>You're in! Here are your tickets for <strong>{event_title}</strong>.</p>"
                f"<p>Total paid: <strong>{total}</strong></p>"
                f"<table>{ticket_rows}</table>"
                f"<p><a href='{order_url}'>View your order any time</a></p>"
                "<p>Keep this email — your codes are your admission.</p>"
                "<p>&mdash; EventNXT</p>"
            ),
        )
        return True
    except (EmailNotConfigured, EmailSendError):
        return False