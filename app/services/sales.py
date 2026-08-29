from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from app.models.promo_code import PromoCode, RewardType
from app.models.promo_code_points_rate import PromoCodePointsRate
from app.models.sale import Sale, SaleSource
from app.models.sales_config import SalesPlatform

# Which platforms currently have a real, working live-data integration.
# Empty today — no live integrations exist yet. Every platform not in
# this set falls back to CSV upload, which is why adding a new
# integration later is additive (add the platform here + implement its
# adapter) rather than a restructuring of anything else in this module.
PLATFORMS_WITH_LIVE_API: set = set()

PLATFORM_LABELS = {
    SalesPlatform.CUSTOM_CSV: "Custom / CSV only",
    SalesPlatform.EVENTBRITE: "Eventbrite",
    SalesPlatform.TICKETMASTER: "Ticketmaster",
    SalesPlatform.SQUARE: "Square",
    SalesPlatform.STRIPE: "Stripe",
    SalesPlatform.OTHER: "Other",
}


def platform_options():
    """The full selectable list for the sales-config setup UI, each
    flagged with whether a live integration currently backs it."""
    return [
        {
            "value": platform.value,
            "label": PLATFORM_LABELS[platform],
            "has_live_api": platform in PLATFORMS_WITH_LIVE_API,
        }
        for platform in SalesPlatform
    ]


def replace_points_rates(db: Session, promo_code_id: str, items) -> None:
    """Wholesale replace a points-type code's per-ticket-type earning
    rates with `items` (objects with .ticket_type and .points)."""
    db.query(PromoCodePointsRate).filter(PromoCodePointsRate.promo_code_id == promo_code_id).delete()
    for item in items:
        db.add(PromoCodePointsRate(promo_code_id=promo_code_id, ticket_type=item.ticket_type, points=item.points))


def points_for_ticket_type(db: Session, promo_code_id: str, ticket_type: Optional[str]) -> int:
    """
    Points earned for one sale of `ticket_type` under this points-type
    code. 0 (not an error, not None) when the sale has no ticket type at
    all, or when this code simply has no configured rate for that type —
    there's nothing ambiguous about "this ticket type wasn't set up to
    earn points," unlike a percentage reward missing the sale amount it
    fundamentally needs.
    """
    if not ticket_type:
        return 0
    rate = (
        db.query(PromoCodePointsRate)
        .filter(PromoCodePointsRate.promo_code_id == promo_code_id, PromoCodePointsRate.ticket_type.ilike(ticket_type))
        .first()
    )
    return rate.points if rate else 0


def compute_reward(db: Session, promo_code: PromoCode, amount: Optional[Decimal], ticket_type: Optional[str]) -> Optional[Decimal]:
    """
    The reward owed for one sale attributed to `promo_code`. Returns None
    when it genuinely can't be computed (a percentage reward with no
    sale amount on the row) rather than silently defaulting to zero —
    the caller decides how to surface that (e.g. flagging the row for
    manual follow-up). POINTS rewards, by contrast, correctly return 0
    (not None) for an unconfigured ticket type — see
    points_for_ticket_type for why that's a different situation.
    """
    if promo_code.reward_type == RewardType.FLAT_AMOUNT:
        return promo_code.reward_value
    if promo_code.reward_type == RewardType.PERCENTAGE:
        if amount is None:
            return None
        return amount * (promo_code.reward_value / Decimal(100))
    if promo_code.reward_type == RewardType.FREE_TICKETS:
        # Not a dollar figure — a ticket count owed. Returned as-is; it's
        # on the reward-terms' reward_type for the caller to interpret
        # correctly, same as amount vs. count is handled anywhere else
        # reward_value is read.
        return promo_code.reward_value
    if promo_code.reward_type == RewardType.POINTS:
        return Decimal(points_for_ticket_type(db, promo_code.id, ticket_type))
    return None


def reconcile_sale_row(db: Session, event_id: str, row: dict) -> Sale:
    """
    Turns one normalized sales row (buyer_name, buyer_email, amount,
    ticket_type, promo_code (the code string, not the id), sale_date,
    external_transaction_id) into a Sale record, matching the code
    against this event's PromoCodes (case-insensitive) and computing the
    reward at import time. Does NOT commit — caller controls the
    transaction so a whole batch can be committed together.
    """
    promo_code = None
    code_text = (row.get("promo_code") or "").strip()
    if code_text:
        promo_code = (
            db.query(PromoCode)
            .filter(PromoCode.event_id == event_id, PromoCode.code.ilike(code_text))
            .first()
        )

    amount = row.get("amount")
    ticket_type = row.get("ticket_type")
    reward = compute_reward(db, promo_code, amount, ticket_type) if promo_code else None

    sale = Sale(
        event_id=event_id,
        promo_code_id=promo_code.id if promo_code else None,
        buyer_name=row.get("buyer_name"),
        buyer_email=row.get("buyer_email"),
        amount=amount,
        ticket_type=ticket_type,
        sale_date=row.get("sale_date"),
        external_transaction_id=row.get("external_transaction_id") or None,
        source=SaleSource.CSV_UPLOAD,
        computed_reward=reward,
    )
    db.add(sale)
    return sale


def existing_transaction_ids(db: Session, event_id: str, transaction_ids: list) -> set:
    """
    Which of these external_transaction_ids are already logged for this
    event — used to skip re-importing the same sale twice when a box
    office export is a full historical snapshot rather than
    only-new-rows. Rows with no transaction_id at all aren't covered by
    this check (there's nothing to dedupe on), so re-uploads without IDs
    are the organizer's own responsibility to avoid.
    """
    if not transaction_ids:
        return set()
    rows = (
        db.query(Sale.external_transaction_id)
        .filter(Sale.event_id == event_id, Sale.external_transaction_id.in_(transaction_ids))
        .all()
    )
    return {r[0] for r in rows}