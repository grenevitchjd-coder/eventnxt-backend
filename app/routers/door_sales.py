# eventnxt-backend: app/routers/door_sales.py
"""
Door sales (0053): a staff member with Guest list access rings up a
walk-up ticket from a tablet/computer at the door, the buyer pays cash
in person, and the sale is recorded instantly — no card, no Stripe
transaction of any kind. Same access as Guest list on purpose (the
organizer's own call): whoever can already edit the roster can also
sell at the door.

Reuses the SAME inventory machinery as the public checkout
(ticketing.create_pending_order / fulfill_paid_order) so a cash sale
locks and decrements the identical live availability a buyer sees on
the public page — full catalog, assigned seats, sectioned pools, passes,
promo codes, everything. The only things a cash sale skips: Stripe
entirely, the platform fee (organizer's explicit call — 0 for now), and
the buyer's own terms checkbox (staff attests on their behalf here).

No fee currently means platform_fee_cents=0 / reserve_cents=0 on these
orders — Earnings, refunds, and the availability math all already work
generically off Order rows and need no special-casing for that.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.event_profile import EventProfile
from app.models.order import Order, OrderStatus
from app.models.order_item import OrderItem
from app.models.promo_code import PromoCode
from app.models.seat import Seat
from app.models.ticket import Ticket
from app.models.ticket_type import TicketType
from app.schemas.ticketing import (
    DoorSaleRequest,
    DoorSaleResponse,
    DoorSaleTicketResponse,
    DoorSalesReconciliationResponse,
    PublicPromoCodeCheckResponse,
    PublicSeatMapResponse,
    PublicTicketTypeResponse,
)
from app.services import seating, ticketing
from app.services.deps import CurrentUser
from app.services.native_sales import record_native_sales
from app.services.permissions import require_guest_list

router = APIRouter(prefix="/events/{event_id}/door-sales", tags=["door-sales"])


@router.get("/catalog", response_model=list[PublicTicketTypeResponse])
def door_sales_catalog(
    event_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_guest_list),
):
    """The same live catalog the public ticket page shows — full-catalog
    parity is deliberate (multi-day passes, assigned seats, everything).
    Unlike the public route this doesn't require the event page to be
    published: staff can sell at the door of a private/unpublished event."""
    return ticketing.ticket_catalog(db, event_id)


@router.get("/ticket-types/{ticket_type_id}/seats", response_model=PublicSeatMapResponse)
def door_sales_seat_map(
    event_id: str,
    ticket_type_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_guest_list),
):
    try:
        return ticketing.seat_map_for_type(db, event_id, ticket_type_id)
    except ticketing.CheckoutError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/promo-codes/{code}", response_model=PublicPromoCodeCheckResponse)
def door_sales_check_promo_code(
    event_id: str,
    code: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_guest_list),
):
    promo = (
        db.query(PromoCode)
        .filter(PromoCode.event_id == event_id, PromoCode.code.ilike(code.strip()))
        .first()
    )
    if not promo:
        return PublicPromoCodeCheckResponse(valid=False)
    return PublicPromoCodeCheckResponse(
        valid=True,
        discount_type=promo.discount_type,
        discount_value=float(promo.discount_value) if promo.discount_value is not None else None,
    )


@router.post("/sell", response_model=DoorSaleResponse)
def sell_at_door(
    event_id: str,
    payload: DoorSaleRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_guest_list),
):
    """
    Rings up and IMMEDIATELY fulfills a cash sale — there's no pending
    state to speak of, the cash is already in hand. Mirrors
    checkout.start_checkout's $0-order path (create → fulfill → record
    → commit → best-effort email) but skips Stripe and the buyer-facing
    terms checkbox entirely.
    """
    if not payload.staff_attested_terms:
        raise HTTPException(
            status_code=400,
            detail="Please confirm the buyer was walked through the Ticket Purchasing Agreement before completing the sale.",
        )

    promo_code = None
    code_text = (payload.promo_code or "").strip()
    if code_text:
        promo_code = (
            db.query(PromoCode)
            .filter(PromoCode.event_id == event_id, PromoCode.code.ilike(code_text))
            .first()
        )
        if not promo_code:
            raise HTTPException(status_code=400, detail="That promo code isn't recognized for this event.")

    try:
        order = ticketing.create_pending_order(
            db,
            event_id=event_id,
            buyer_name=payload.buyer_name,
            buyer_email=payload.buyer_email,
            requested=[
                (item.ticket_type_id, item.quantity, item.seat_ids, item.zone_section_id, item.zone_section_ids)
                for item in payload.items
            ],
            promo_code=promo_code,
        )
    except ticketing.CheckoutError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))

    # Cash, at the door: no fee for now (organizer's explicit call), no
    # Stripe fields touched at all, paid the instant it's rung up.
    order.payment_method = "cash"
    order.sold_by_user_id = user.user_id
    order.sold_by_name = user.name
    order.platform_fee_cents = 0
    order.reserve_cents = 0
    order.organizer_net_cents = order.subtotal_cents - order.discount_cents
    order.terms_accepted_at = datetime.now(timezone.utc)
    order.marketing_opt_in = bool(payload.marketing_opt_in)
    order.expires_at = None  # paid instantly — nothing to expire

    tickets = ticketing.fulfill_paid_order(db, order)
    record_native_sales(db, order)
    db.commit()

    profile = db.query(EventProfile).filter(EventProfile.event_id == event_id).first()
    event_title = profile.title if profile else "your event"
    order_url = (
        f"{settings.eventnxt_frontend_url}/e/{profile.slug}/order/{order.order_token}" if profile else ""
    )
    # Best-effort: the sale stands regardless of whether the email goes
    # out — the on-screen QR (below) is what actually gets the buyer in.
    email_sent = ticketing.send_order_confirmation_email(db, order, tickets, event_title, order_url)

    seat_ids = [t.seat_id for t in tickets if t.seat_id]
    seats_by_id = {s.id: s for s in db.query(Seat).filter(Seat.id.in_(seat_ids)).all()} if seat_ids else {}
    # Section-sold (unassigned) tickets carry no seat_id — the section
    # the staffer picked lives on the order item instead (a snapshot
    # ALREADY formatted with the pool's unit_label vocabulary at
    # purchase time, see seats.lock_and_claim_section). Missing this
    # fallback here is exactly what made a door-sold section ticket
    # unidentifiable on screen.
    item_ids = {t.order_item_id for t in tickets if t.order_item_id}
    section_by_item = (
        {i.id: i.section_label for i in db.query(OrderItem).filter(OrderItem.id.in_(item_ids)).all()}
        if item_ids
        else {}
    )
    type_ids = {t.ticket_type_id for t in tickets}
    tts_by_id = {t.id: t for t in db.query(TicketType).filter(TicketType.id.in_(type_ids)).all()} if type_ids else {}

    return DoorSaleResponse(
        order_token=order.order_token,
        total_cents=order.subtotal_cents - order.discount_cents,
        email_sent=email_sent,
        tickets=[
            DoorSaleTicketResponse(
                code=t.code,
                ticket_type_name=tts_by_id[t.ticket_type_id].name if t.ticket_type_id in tts_by_id else "Ticket",
                seat_label=seating.format_seat_label(db, seats_by_id.get(t.seat_id)) or section_by_item.get(t.order_item_id),
                valid_date=t.valid_date,
            )
            for t in tickets
        ],
    )


@router.get("/reconciliation", response_model=DoorSalesReconciliationResponse)
def door_sales_reconciliation(
    event_id: str,
    start: datetime = Query(..., description="Inclusive UTC instant for local midnight — device computes this"),
    end: datetime = Query(..., description="Exclusive UTC instant for the following local midnight"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_guest_list),
):
    """
    ONE total for the window the door screen asks about — deliberately
    not broken out per staff member (organizer's call). The window is
    computed CLIENT-SIDE from the device's local midnight-to-midnight,
    same "trust the door's local day" philosophy check-in already uses,
    just expressed as a UTC instant range instead of a bare date string
    since we're bucketing timestamps here, not comparing to a stored day.
    """
    orders = (
        db.query(Order)
        .filter(
            Order.event_id == event_id,
            Order.payment_method == "cash",
            Order.status == OrderStatus.PAID,
            Order.paid_at >= start,
            Order.paid_at < end,
        )
        .all()
    )
    order_ids = [o.id for o in orders]
    ticket_count = (
        db.query(Ticket).filter(Ticket.order_id.in_(order_ids)).count() if order_ids else 0
    )
    cash_total_cents = sum(o.subtotal_cents - o.discount_cents for o in orders)
    return DoorSalesReconciliationResponse(
        date=start.date().isoformat(),
        cash_total_cents=cash_total_cents,
        ticket_count=ticket_count,
        sale_count=len(orders),
    )