"""
eventnxt-backend: app/routers/orders_admin.py

Organizer-side order management: the list/search view and the refund
action. This is the door-day tool too — "what's the name on the card?"
gets answered by the search box here until QR check-in exists.

Refund policy, exactly as decided: organizer-initiated, FULL order only
(v1), buyer gets 100% back, EventNXT's platform fee is returned
(ledger arithmetic — Phase 3 machinery), Stripe's kept processing fee is
borne by the organizer, tickets void, and inventory releases back to the
pool automatically — a REFUNDED order simply stops counting in the
availability math, the same trick expired holds use.

Referral consequences of a refund: the order's NATIVE Sale rows are
DELETED — a referrer shouldn't keep earning on money that went back, and
points balances (derived from sale sums) drop accordingly. Already-
awarded bonus tiers are deliberately LEFT INTACT: awards snapshot at the
moment of crossing (the house snapshot rule), and clawing them back
retroactively would rewrite history the referrer may have already
redeemed against.
"""

from datetime import datetime, timezone

import stripe as stripe_lib
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.order import Order, OrderStatus
from app.models.order_item import OrderItem
from app.models.sale import Sale
from app.models.ticket import Ticket, TicketStatus
from app.schemas.ticketing import AdminOrderResponse, AdminOrderItem
from app.services.deps import CurrentUser
from app.services.email import EmailNotConfigured, EmailSendError, send_email
from app.services.event_access import require_event_access
from app.services.stripe_gateway import create_refund

router = APIRouter(tags=["orders-admin"])


def _serialize(order: Order, items: list[OrderItem], ticket_count: int) -> AdminOrderResponse:
    return AdminOrderResponse(
        id=order.id,
        status=order.status.value,
        buyer_name=order.buyer_name,
        buyer_email=order.buyer_email,
        currency=order.currency,
        subtotal_cents=order.subtotal_cents,
        discount_cents=order.discount_cents,
        platform_fee_cents=order.platform_fee_cents,
        organizer_net_cents=order.organizer_net_cents,
        order_token=order.order_token,
        items=[
            AdminOrderItem(
                ticket_type_name=i.ticket_type_name, quantity=i.quantity, unit_price_cents=i.unit_price_cents
            )
            for i in items
        ],
        ticket_count=ticket_count,
        created_at=order.created_at,
        paid_at=order.paid_at,
        refunded_at=order.refunded_at,
    )


@router.get("/events/{event_id}/orders", response_model=list[AdminOrderResponse])
def list_orders(
    event_id: str,
    search: str = "",
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    q = db.query(Order).filter(Order.event_id == event_id)
    term = search.strip()
    if term:
        like = f"%{term}%"
        q = q.filter((Order.buyer_email.ilike(like)) | (Order.buyer_name.ilike(like)))
    orders = q.order_by(Order.created_at.desc()).limit(200).all()

    order_ids = [o.id for o in orders]
    items_by_order: dict = {}
    for item in db.query(OrderItem).filter(OrderItem.order_id.in_(order_ids)).all() if order_ids else []:
        items_by_order.setdefault(item.order_id, []).append(item)
    tickets_by_order: dict = {}
    for t in db.query(Ticket).filter(Ticket.order_id.in_(order_ids)).all() if order_ids else []:
        tickets_by_order[t.order_id] = tickets_by_order.get(t.order_id, 0) + 1

    return [
        _serialize(o, items_by_order.get(o.id, []), tickets_by_order.get(o.id, 0)) for o in orders
    ]


@router.post("/events/{event_id}/orders/{order_id}/refund", response_model=AdminOrderResponse)
def refund_order(
    event_id: str,
    order_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    order = (
        db.query(Order)
        .filter(Order.id == order_id, Order.event_id == event_id)
        .with_for_update()
        .first()
    )
    if not order:
        raise HTTPException(status_code=404, detail="Order not found.")
    if order.status != OrderStatus.PAID:
        raise HTTPException(status_code=400, detail=f"Only paid orders can be refunded (this one is {order.status.value}).")

    # Money first, records second: if Stripe refuses, nothing local changes.
    # A $0/comp order has no payment intent — nothing to refund at Stripe,
    # the local reversal below is the whole action.
    if order.stripe_payment_intent_id:
        try:
            create_refund(order.stripe_payment_intent_id)
        except stripe_lib.error.StripeError as exc:
            raise HTTPException(status_code=502, detail=f"Stripe refused the refund: {getattr(exc, 'user_message', None) or 'try again.'}")

    order.status = OrderStatus.REFUNDED
    order.refunded_at = datetime.now(timezone.utc)

    tickets = db.query(Ticket).filter(Ticket.order_id == order.id).all()
    for t in tickets:
        t.status = TicketStatus.REFUNDED

    # Reverse referral attribution (see module docstring for the policy).
    db.query(Sale).filter(Sale.external_transaction_id.like(f"eventnxt-{order.id}-%")).delete(
        synchronize_session=False
    )

    items = db.query(OrderItem).filter(OrderItem.order_id == order.id).all()
    db.commit()

    # Best-effort notice to the buyer — the refund stands regardless.
    try:
        send_email(
            to=order.buyer_email,
            subject="Your order was refunded",
            text_body=(
                f"Hi {order.buyer_name},\n\n"
                f"Your order has been refunded in full. The amount will return to your "
                f"original payment method (card refunds typically take 5-10 business days "
                f"to appear).\n\nThe ticket codes from this order are no longer valid.\n\n— EventNXT"
            ),
        )
    except (EmailNotConfigured, EmailSendError):
        pass

    return _serialize(order, items, len(tickets))