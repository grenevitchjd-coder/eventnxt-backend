"""
eventnxt-backend: app/routers/checkout.py

The public face of ticket sales — no authentication anywhere here, by
design (buyers have no accounts; possession of an order_token IS
ownership, same philosophy as rsvp_token).

The webhook is the source of truth for payment, never the success
redirect: buyers close tabs, redirects fail, but Stripe retries webhooks
until we 200 them.
"""

import uuid

import stripe as stripe_lib
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.event_profile import EventProfile
from app.models.order import Order, OrderStatus
from app.models.order_item import OrderItem
from app.models.promo_code import PromoCode
from app.models.stripe_webhook_event import StripeWebhookEvent
from app.models.seat import Seat
from app.models.zone_section import ZoneSection
from app.models.seating_category import SeatingCategory
from app.models.ticket import Ticket
from app.models.ticket_type import TicketType
from app.schemas.ticketing import (
    CheckoutRequest,
    FindMyTicketsRequest,
    PublicPromoCodeCheckResponse,
    CheckoutResponse,
    PublicOrderItemResponse,
    PublicOrderResponse,
    PublicTicketResponse,
    PublicTicketTypeResponse,
    PublicSeatMapResponse,
    PublicTicketSectionOption,
    PublicSeatResponse,
    PublicSeatSectionResponse,
)
from app.services import seats as seats_service
from app.services import ticketing
from app.services.native_sales import record_native_sales
from app.services.stripe_gateway import WebhookNotConfigured, construct_webhook_event, create_checkout_session

router = APIRouter(tags=["checkout"])


def _published_profile_or_404(db: Session, slug: str) -> EventProfile:
    profile = (
        db.query(EventProfile)
        .filter(EventProfile.slug == slug, EventProfile.is_published.is_(True))
        .first()
    )
    if not profile:
        raise HTTPException(status_code=404, detail="Event not found.")
    return profile


@router.get("/public/events/{slug}/ticket-types", response_model=list[PublicTicketTypeResponse])
def list_public_ticket_types(slug: str, db: Session = Depends(get_db)):
    """
    The picker's data. Inactive types are hidden entirely; active-but-not-
    on-sale ones are shown with on_sale=false so the page can say "sales
    open soon" / "sold out" instead of pretending the type doesn't exist.
    """
    profile = _published_profile_or_404(db, slug)
    ticket_types = (
        db.query(TicketType)
        .filter(TicketType.event_id == profile.event_id, TicketType.is_active.is_(True))
        .order_by(TicketType.sort_order, TicketType.created_at)
        .all()
    )
    avail = ticketing.availability_for(db, ticket_types) if ticket_types else {}
    pool_ids = [t.seating_category_id for t in ticket_types if t.seating_category_id]
    pools = db.query(SeatingCategory).filter(SeatingCategory.id.in_(pool_ids)).all() if pool_ids else []
    assigned_pool_ids = {c.id for c in pools if c.sales_grain == "seat"}
    section_pool_ids = seats_service.section_required_pool_ids(db, pool_ids)
    sections_by_pool = {}
    if section_pool_ids:
        for sec in (
            db.query(ZoneSection)
            .filter(ZoneSection.seating_category_id.in_(list(section_pool_ids)))
            .order_by(ZoneSection.sort_order)
            .all()
        ):
            sections_by_pool.setdefault(sec.seating_category_id, []).append(
                PublicTicketSectionOption(
                    id=sec.id,
                    section_label=sec.section_label,
                    row_label=sec.row_label,
                    remaining=max(sec.capacity - seats_service.section_heads_taken(db, sec.id), 0),
                )
            )
    return [
        PublicTicketTypeResponse(
            id=t.id,
            name=t.name,
            description=t.description,
            price_cents=t.price_cents,
            currency=t.currency,
            max_per_order=t.max_per_order,
            admits=t.admits or 1,
            assigned_seating=t.seating_category_id in assigned_pool_ids,
            section_required=t.seating_category_id in section_pool_ids,
            sections=sections_by_pool.get(t.seating_category_id, []),
            available=avail[t.id]["available"],
            on_sale=ticketing.is_on_sale(t, avail[t.id]["available"]),
        )
        for t in ticket_types
    ]


@router.get("/public/events/{slug}/ticket-types/{ticket_type_id}/seats", response_model=PublicSeatMapResponse)
def public_seat_map(slug: str, ticket_type_id: uuid.UUID, db: Session = Depends(get_db)):
    """
    The seat picker's data: every seat of the ticket type's pool grouped
    by section, flagged available or not (sold, held on a live order, or
    organizer-blocked all read as unavailable). Serving the FULL map —
    not just open seats — lets the schematic gray out what's gone.
    """
    profile = _published_profile_or_404(db, slug)
    tt = (
        db.query(TicketType)
        .filter(TicketType.id == ticket_type_id, TicketType.event_id == profile.event_id)
        .first()
    )
    if not tt or not tt.seating_category_id:
        raise HTTPException(status_code=404, detail="Ticket type not found.")
    seats = (
        db.query(Seat)
        .filter(Seat.seating_category_id == tt.seating_category_id)
        .order_by(Seat.section_label, Seat.row_label, Seat.seat_number)
        .all()
    )
    taken = seats_service.taken_seat_ids(db, [s.id for s in seats])
    grouped = {}
    for seat in seats:
        grouped.setdefault((seat.section_label, seat.row_label), []).append(seat)
    return PublicSeatMapResponse(
        ticket_type_id=tt.id,
        sections=[
            PublicSeatSectionResponse(
                section_label=sec,
                row_label=row,
                seats=[
                    PublicSeatResponse(id=x.id, seat_number=x.seat_number, available=(x.id not in taken and not x.is_blocked))
                    for x in xs
                ],
            )
            for (sec, row), xs in grouped.items()
        ],
    )


@router.post("/public/events/{slug}/checkout", response_model=CheckoutResponse)
def start_checkout(slug: str, payload: CheckoutRequest, db: Session = Depends(get_db)):
    profile = _published_profile_or_404(db, slug)

    # Promo code: resolve before anything else — a bad code should fail
    # fast, before inventory is held or payment started.
    promo_code = None
    code_text = (payload.promo_code or "").strip()
    if code_text:
        promo_code = (
            db.query(PromoCode)
            .filter(PromoCode.event_id == profile.event_id, PromoCode.code.ilike(code_text))
            .first()
        )
        if not promo_code:
            raise HTTPException(status_code=400, detail="That promo code isn't recognized for this event.")

    try:
        order = ticketing.create_pending_order(
            db,
            event_id=profile.event_id,
            buyer_name=payload.buyer_name,
            buyer_email=payload.buyer_email,
            requested=[(item.ticket_type_id, item.quantity, item.seat_ids, item.zone_section_id) for item in payload.items],
            promo_code=promo_code,
        )
    except ticketing.CheckoutError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))

    amount_due = order.subtotal_cents - order.discount_cents

    order_url = f"{settings.eventnxt_frontend_url}/e/{slug}/order/{order.order_token}"

    # $0-DUE order (free ticket types, or a 100%-off code): no Stripe,
    # no fee — paid instantly, tickets minted now, sales recorded, email
    # fired best-effort.
    if amount_due == 0:
        tickets = ticketing.fulfill_paid_order(db, order)
        record_native_sales(db, order)
        db.commit()
        ticketing.send_order_confirmation_email(order, tickets, profile.title, order_url)
        return CheckoutResponse(
            order_token=order.order_token, checkout_url=None, total_cents=0, status="paid"
        )

    items = db.query(OrderItem).filter(OrderItem.order_id == order.id).all()
    try:
        session = create_checkout_session(
            order,
            line_items_data=[
                {
                    "name": i.ticket_type_name,
                    "unit_price_cents": i.unit_price_cents,
                    "quantity": i.quantity,
                    "currency": order.currency,
                }
                for i in items
            ],
            success_url=order_url,
            cancel_url=f"{settings.eventnxt_frontend_url}/e/{slug}",
            discount_cents=order.discount_cents,
            discount_label=(promo_code.code.upper() if promo_code else None),
        )
    except stripe_lib.error.StripeError:
        # The hold self-expires in 30 min either way; surface a clean error.
        db.rollback()
        raise HTTPException(status_code=502, detail="Could not start payment — please try again.")

    order.stripe_checkout_session_id = session.id
    db.commit()

    return CheckoutResponse(
        order_token=order.order_token,
        checkout_url=session.url,
        total_cents=amount_due,
        status="pending",
    )



@router.get("/public/events/{slug}/promo-codes/{code}", response_model=PublicPromoCodeCheckResponse)
def check_public_promo_code(slug: str, code: str, db: Session = Depends(get_db)):
    """
    Buyer-facing code check for the live "-$4.00 applied" display. Exposes
    ONLY the buyer-relevant discount terms — never the referrer's reward
    terms, which are the referrer's business.
    """
    profile = _published_profile_or_404(db, slug)
    promo = (
        db.query(PromoCode)
        .filter(PromoCode.event_id == profile.event_id, PromoCode.code.ilike(code.strip()))
        .first()
    )
    if not promo:
        return PublicPromoCodeCheckResponse(valid=False)
    return PublicPromoCodeCheckResponse(
        valid=True,
        discount_type=promo.discount_type,
        discount_value=float(promo.discount_value) if promo.discount_value is not None else None,
    )


@router.post("/public/events/{slug}/promo-codes/{code}/click", status_code=204)
def record_promo_link_click(slug: str, code: str, db: Session = Depends(get_db)):
    """
    Tracked-link landing (/e/<slug>?ref=CODE). Always 204 — an
    unrecognized code reveals nothing (no code enumeration via response
    differences). Atomic SQL increment: two simultaneous clicks both
    count.
    """
    profile = _published_profile_or_404(db, slug)
    db.query(PromoCode).filter(
        PromoCode.event_id == profile.event_id, PromoCode.code.ilike(code.strip())
    ).update({PromoCode.link_clicks: PromoCode.link_clicks + 1}, synchronize_session=False)
    db.commit()



# In-memory cooldown for the lookup mailer — per-process, resets on dyno
# restart, which is honest-enough abuse protection for v1 on a single web
# dyno (the worst case after a restart is one extra email). Upgrade to a
# real table if this ever runs on multiple dynos.
_find_tickets_last_sent: dict[str, float] = {}
_FIND_TICKETS_COOLDOWN_SECONDS = 300


@router.post("/public/events/{slug}/find-my-tickets", status_code=200)
def find_my_tickets(slug: str, payload: FindMyTicketsRequest, db: Session = Depends(get_db)):
    """
    Self-serve ticket recovery, the way real ticket companies do it:
    NEVER display tickets for a typed email — send them TO that email.
    Possession of the inbox is the authentication. The response is
    identical whether or not orders exist (no probing which emails
    bought), same philosophy as unpublished pages 404ing identically.
    """
    import time

    profile = _published_profile_or_404(db, slug)
    email = payload.email.strip().lower()

    now = time.monotonic()
    last = _find_tickets_last_sent.get(email)
    generic = {"status": "ok"}
    if last is not None and now - last < _FIND_TICKETS_COOLDOWN_SECONDS:
        return generic  # silently rate-limited — still the identical response
    _find_tickets_last_sent[email] = now

    orders = (
        db.query(Order)
        .filter(
            Order.event_id == profile.event_id,
            Order.buyer_email == email,
            Order.status.in_([OrderStatus.PAID, OrderStatus.REFUNDED]),
        )
        .order_by(Order.created_at)
        .all()
    )
    if not orders:
        return generic

    lines = []
    for o in orders:
        items = db.query(OrderItem).filter(OrderItem.order_id == o.id).all()
        summary = ", ".join(f"{i.quantity}x {i.ticket_type_name}" for i in items)
        note = " (refunded)" if o.status == OrderStatus.REFUNDED else ""
        lines.append(
            f"- {summary}{note}\n  {settings.eventnxt_frontend_url}/e/{slug}/order/{o.order_token}"
        )

    try:
        from app.services.email import send_email

        send_email(
            to=email,
            subject=f"Your tickets for {profile.title}",
            text_body=(
                f"Here are the ticket orders we have for this email at {profile.title}:\n\n"
                + "\n\n".join(lines)
                + "\n\nEach link opens that order's tickets. Keep this email handy at the door.\n\n"
                + "— EventNXT"
            ),
        )
    except Exception:
        pass  # identical outward behavior even if the mailer hiccups

    return generic


@router.get("/public/tickets/{code}/qr.png")
def public_ticket_qr(code: str, db: Session = Depends(get_db)):
    """
    The scannable form of a ticket code, as a PNG — embedded by the
    order/comp emails and loadable anywhere a code is shown. Knowing the
    code IS the credential (same trust as the code printed beside it),
    so the QR adds no new exposure; unknown codes 404 so this can't be
    used as an oracle beyond code validity, which Find My Tickets
    already exposes.
    """
    import io

    import segno
    from fastapi.responses import Response

    clean = code.strip().upper()
    exists = db.query(Ticket.id).filter(Ticket.code == clean).first()
    if not exists:
        raise HTTPException(status_code=404, detail="Unknown ticket code.")
    buf = io.BytesIO()
    # micro=False is load-bearing: segno auto-selects Micro QR for short
    # payloads, and Micro QR is unreadable to jsQR, BarcodeDetector, and
    # many camera apps. Ticket codes are short; force full QR.
    segno.make(clean, error="m", micro=False).save(buf, kind="png", scale=6, border=2)
    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400, immutable"},
    )


@router.get("/public/orders/{order_token}", response_model=PublicOrderResponse)
def get_public_order(order_token: str, db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.order_token == order_token).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found.")

    profile = db.query(EventProfile).filter(EventProfile.event_id == order.event_id).first()
    items = db.query(OrderItem).filter(OrderItem.order_id == order.id).all()
    tickets = db.query(Ticket).filter(Ticket.order_id == order.id).all()
    seat_ids = [t.seat_id for t in tickets if t.seat_id]
    seat_labels = (
        {x.id: x.label for x in db.query(Seat).filter(Seat.id.in_(seat_ids)).all()} if seat_ids else {}
    )

    return PublicOrderResponse(
        status=order.status.value,
        event_slug=profile.slug if profile else "",
        event_title=profile.title if profile else "",
        buyer_name=order.buyer_name,
        buyer_email=order.buyer_email,
        currency=order.currency,
        subtotal_cents=order.subtotal_cents,
        discount_cents=order.discount_cents,
        items=[
            PublicOrderItemResponse(
                ticket_type_name=i.ticket_type_name, quantity=i.quantity, unit_price_cents=i.unit_price_cents,
                section_label=i.section_label,
            )
            for i in items
        ],
        tickets=[
            PublicTicketResponse(
                code=t.code,
                ticket_type_name=_ticket_type_name(items, t),
                status=t.status.value,
                seat_label=seat_labels.get(t.seat_id) or _section_label_for(items, t),
            )
            for t in tickets
        ],
        paid_at=order.paid_at,
        refund_policy=profile.refund_policy if profile else None,
    )


def _section_label_for(items, ticket):
    """Section-sold tickets: surface the order item's section where an
    assigned ticket would show its seat."""
    for i in items:
        if i.id == ticket.order_item_id:
            return i.section_label
    return None


def _ticket_type_name(items: list[OrderItem], ticket: Ticket) -> str:
    for i in items:
        if i.id == ticket.order_item_id:
            return i.ticket_type_name
    return ""


# ---------- Stripe webhook ----------


@router.post("/webhooks/stripe")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")

    try:
        event = construct_webhook_event(payload, signature)
    except WebhookNotConfigured:
        raise HTTPException(status_code=503, detail="Webhook not configured.")
    except (ValueError, stripe_lib.error.SignatureVerificationError):
        raise HTTPException(status_code=400, detail="Invalid signature.")

    # Idempotency: the unique constraint is the referee. Stripe redelivers
    # events by design — a second delivery hits the constraint and gets a
    # clean 200 no-op instead of a double-fulfilled order.
    db.add(StripeWebhookEvent(stripe_event_id=event["id"], event_type=event["type"]))
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return {"status": "already_processed"}

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        order = (
            db.query(Order).filter(Order.stripe_checkout_session_id == session["id"]).with_for_update().first()
        )
        if order and order.status == OrderStatus.PENDING:
            order.stripe_payment_intent_id = session.get("payment_intent")
            tickets = ticketing.fulfill_paid_order(db, order)
            record_native_sales(db, order)  # feeds the referral machine — same transaction as the paid order
            profile = db.query(EventProfile).filter(EventProfile.event_id == order.event_id).first()
            db.commit()  # the paid order is sacred — committed BEFORE any email attempt
            if profile:
                order_url = f"{settings.eventnxt_frontend_url}/e/{profile.slug}/order/{order.order_token}"
                ticketing.send_order_confirmation_email(order, tickets, profile.title, order_url)
            return {"status": "fulfilled"}
        db.commit()  # record the webhook event even if the order wasn't actionable
        return {"status": "no_action"}

    if event["type"] == "checkout.session.expired":
        session = event["data"]["object"]
        order = db.query(Order).filter(Order.stripe_checkout_session_id == session["id"]).first()
        if order and order.status == OrderStatus.PENDING:
            order.status = OrderStatus.EXPIRED  # the timestamp already freed the hold; this is bookkeeping
        db.commit()
        return {"status": "expired"}

    db.commit()  # unhandled event types: record and acknowledge
    return {"status": "ignored"}