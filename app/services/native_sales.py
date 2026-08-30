"""
eventnxt-backend: app/services/native_sales.py

The bridge between native ticket sales and the referral machine. When an
order is PAID (webhook or the $0 instant path), this records one Sale
row per order item with source=NATIVE — the same normalized shape the
CSV importer produces — so promo attribution, reward computation, points
earning, and bonus tiers work IDENTICALLY regardless of where a sale
came from. The platform registry was designed for exactly this moment:
a live source is just one more feeder into the same reconciled table.

Two deliberate differences from the CSV path:
- Attribution is by promo_code_id, not text matching: the code was
  validated at checkout, BEFORE payment — known at purchase, never
  guessed afterward.
- external_transaction_id is synthesized from order+item ids, so if a
  webhook ever replays past the idempotency gate (it shouldn't), the
  CSV-era dedup would still catch the duplicate.
"""

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.order import Order
from app.models.order_item import OrderItem
from app.models.promo_code import PromoCode
from app.models.sale import Sale, SaleSource
from app.services.bonuses import check_and_award_bonuses
from app.services.sales import compute_reward


def record_native_sales(db: Session, order: Order) -> list[Sale]:
    """
    Creates NATIVE Sale rows for a just-paid order and, if a promo code
    is attached, fires the bonus-tier check. Does NOT commit — the caller
    owns the transaction (the paid order, its tickets, and its sales
    should land atomically).
    """
    items = db.query(OrderItem).filter(OrderItem.order_id == order.id).all()

    promo_code = None
    if order.promo_code_id:
        promo_code = db.query(PromoCode).filter(PromoCode.id == order.promo_code_id).first()

    # The discount is order-level but Sale rows are per-item, and
    # percentage REWARDS must compute on money the buyer actually paid —
    # so allocate the discount across items proportionally to their face
    # value, with the final item absorbing rounding remainder so the
    # cents always sum exactly.
    face_values = [item.unit_price_cents * item.quantity for item in items]
    total_face = sum(face_values) or 1
    allocated = [round(order.discount_cents * fv / total_face) for fv in face_values]
    if allocated:
        allocated[-1] = order.discount_cents - sum(allocated[:-1])

    sale_date = datetime.now(timezone.utc).date().isoformat()
    sales: list[Sale] = []
    for item, face, disc in zip(items, face_values, allocated):
        # Sale.amount is Numeric DOLLARS (the CSV-era convention);
        # ticketing speaks integer cents — convert at this boundary only.
        amount = Decimal(face - disc) / 100
        reward = (
            compute_reward(db, promo_code, amount, item.ticket_type_name, item.quantity)
            if promo_code
            else None
        )
        sale = Sale(
            event_id=order.event_id,
            promo_code_id=promo_code.id if promo_code else None,
            buyer_name=order.buyer_name,
            buyer_email=order.buyer_email,
            amount=amount,
            ticket_type=item.ticket_type_name,
            quantity=item.quantity,
            sale_date=sale_date,
            external_transaction_id=f"eventnxt-{order.id}-{item.id}",
            source=SaleSource.NATIVE,
            computed_reward=reward,
        )
        db.add(sale)
        sales.append(sale)

    if promo_code:
        # Bonus tiers count SUM(quantity) over this code's sales — which
        # must include the rows added THIS transaction. autoflush is off
        # in this app, so flush explicitly or the check won't see them:
        # the exact bug the CSV import hit on its first live test
        # (handoff doc, Section 5). Never remove this flush.
        db.flush()
        check_and_award_bonuses(db, str(order.event_id), str(promo_code.id))

    return sales