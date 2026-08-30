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
from app.models.ticket import Ticket
from app.models.ticket_type import TicketType
from app.schemas.ticketing import (
    CheckoutRequest,
    CheckoutResponse,
    PublicOrderItemResponse,
    PublicOrderResponse,
    PublicTicketResponse,
    PublicTicketTypeResponse,
)
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
    return [
        PublicTicketTypeResponse(
            id=t.id,
            name=t.name,
            description=t.description,
            price_cents=t.price_cents,
            currency=t.currency,
            max_per_order=t.max_per_order,
            available=avail[t.id]["available"],
            on_sale=ticketing.is_on_sale(t, avail[t.id]["available"]),
        )
        for t in ticket_types
    ]


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
            requested=[(item.ticket_type_id, item.quantity) for item in payload.items],
        )
    except ticketing.CheckoutError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))

    if promo_code:
        order.promo_code_id = promo_code.id

    order_url = f"{settings.eventnxt_frontend_url}/e/{slug}/order/{order.order_token}"

    # $0 order (comps, free RSVP-style types): no Stripe, no fee — paid
    # instantly, tickets minted now, sales recorded, email fired
    # best-effort.
    if order.subtotal_cents == 0:
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
        total_cents=order.subtotal_cents,
        status="pending",
    )


@router.get("/public/orders/{order_token}", response_model=PublicOrderResponse)
def get_public_order(order_token: str, db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.order_token == order_token).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found.")

    profile = db.query(EventProfile).filter(EventProfile.event_id == order.event_id).first()
    items = db.query(OrderItem).filter(OrderItem.order_id == order.id).all()
    tickets = db.query(Ticket).filter(Ticket.order_id == order.id).all()

    return PublicOrderResponse(
        status=order.status.value,
        event_slug=profile.slug if profile else "",
        event_title=profile.title if profile else "",
        buyer_name=order.buyer_name,
        buyer_email=order.buyer_email,
        currency=order.currency,
        subtotal_cents=order.subtotal_cents,
        items=[
            PublicOrderItemResponse(
                ticket_type_name=i.ticket_type_name, quantity=i.quantity, unit_price_cents=i.unit_price_cents
            )
            for i in items
        ],
        tickets=[
            PublicTicketResponse(code=t.code, ticket_type_name=_ticket_type_name(items, t), status=t.status.value)
            for t in tickets
        ],
        paid_at=order.paid_at,
        refund_policy=profile.refund_policy if profile else None,
    )


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