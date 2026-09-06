from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from sqlalchemy import func

from app.models.promo_code import PromoCode, RewardType
from app.models.referral_contact import ReferralContact
from app.models.sale import Sale
from app.models.promo_code_points_rate import PromoCodePointsRate
from app.models.referral_contact import ReferralContact
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


def compute_reward(
    db: Session, promo_code: PromoCode, amount: Optional[Decimal], ticket_type: Optional[str], quantity: int = 1
) -> Optional[Decimal]:
    """
    The reward owed for one sale attributed to `promo_code`. Returns None
    when it genuinely can't be computed (a percentage reward with no
    sale amount on the row) rather than silently defaulting to zero —
    the caller decides how to surface that (e.g. flagging the row for
    manual follow-up). POINTS rewards, by contrast, correctly return 0
    (not None) for an unconfigured ticket type — see
    points_for_ticket_type for why that's a different situation.

    `quantity` is how many tickets this one sale row actually covers (a
    bulk box-office transaction can be more than one). FLAT_AMOUNT,
    FREE_TICKETS, and POINTS are all "per ticket" rewards, so they scale
    with quantity — a 50-ticket sale at $10 flat correctly earns $500,
    not $10. PERCENTAGE does NOT get multiplied here: `amount` already
    represents the sale's full dollar value (50 tickets' worth), so
    multiplying by quantity on top of that would double-count.
    """
    if promo_code.reward_type is None:
        # Self promo (0042) — nobody earns from it, by definition.
        return None
    if promo_code.reward_type == RewardType.FLAT_AMOUNT:
        return promo_code.reward_value * quantity
    if promo_code.reward_type == RewardType.PERCENTAGE:
        if amount is None:
            return None
        return amount * (promo_code.reward_value / Decimal(100))
    if promo_code.reward_type == RewardType.FREE_TICKETS:
        return promo_code.reward_value * quantity
    if promo_code.reward_type == RewardType.POINTS:
        return Decimal(points_for_ticket_type(db, promo_code.id, ticket_type)) * quantity
    return None


def reconcile_sale_row(db: Session, event_id: str, row: dict) -> Sale:
    """
    Turns one normalized sales row (buyer_name, buyer_email, amount,
    ticket_type, quantity, promo_code (the code string, not the id),
    sale_date, external_transaction_id) into a Sale record, matching the
    code against this event's PromoCodes (case-insensitive) and
    computing the reward at import time. Does NOT commit — caller
    controls the transaction so a whole batch can be committed together.
    """
    promo_code = None
    code_text = (row.get("promo_code") or "").strip()
    if code_text:
        promo_code = (
            db.query(PromoCode)
            .filter(PromoCode.event_id == event_id, PromoCode.code.ilike(code_text))
            .first()
        )

    # Per-recipient stamping for external sales (0044) — click-grain is
    # certain, sale-grain is best-effort by email:
    # - codeless row + exactly one contact matching the buyer's email ->
    #   credit that sender (code + person), same single-match rule as
    #   native's device-switch fallback;
    # - row already matched to a code -> stamp the person too, but only
    #   when the single email match belongs to THAT code (a buyer both
    #   invited by Sarah and typing Ben's code stays Ben's sale, personless).
    referral_contact_id = None
    buyer_email = row.get("buyer_email")
    if buyer_email:
        matches = (
            db.query(ReferralContact)
            .filter(ReferralContact.event_id == event_id, ReferralContact.email.ilike(buyer_email.strip()))
            .limit(2)
            .all()
        )
        if len(matches) == 1:
            contact = matches[0]
            if promo_code is None:
                promo_code = db.query(PromoCode).filter(PromoCode.id == contact.promo_code_id).first()
                if promo_code is not None:
                    referral_contact_id = contact.id
            elif promo_code.id == contact.promo_code_id:
                referral_contact_id = contact.id

    amount = row.get("amount")
    ticket_type = row.get("ticket_type")
    quantity = row.get("quantity") or 1
    reward = compute_reward(db, promo_code, amount, ticket_type, quantity) if promo_code else None

    sale = Sale(
        event_id=event_id,
        promo_code_id=promo_code.id if promo_code else None,
        buyer_name=row.get("buyer_name"),
        buyer_email=row.get("buyer_email"),
        amount=amount,
        ticket_type=ticket_type,
        quantity=quantity,
        sale_date=row.get("sale_date"),
        external_transaction_id=row.get("external_transaction_id") or None,
        source=SaleSource.CSV_UPLOAD,
        computed_reward=reward,
        referral_contact_id=referral_contact_id,
        # 0049 stamps from services/sale_matching (default for callers
        # that don't pre-enrich: unstamped admission row -> the same
        # normalized-name fallback pre-0049 rows use).
        seating_category_id=row.get("seating_category_id"),
        event_day=row.get("event_day"),
        is_admission=row.get("is_admission", True),
        all_days=row.get("all_days", False),
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

def imported_heads_for_pool(db: Session, category) -> int:
    """
    Imported (non-native) sold heads counted against one pool — THE
    function both Seating summary and comp-room math call (0049), so the
    number shown and the number enforced are structurally the same.

    Stamped rows (0049+) count by their seating_category_id stamp —
    rename-proof, day-routed at import; all-days packages (0050) count
    into every member of the pool's name family. Unstamped rows (pre-0049, or
    labels nobody has mapped yet) fall back to a NORMALIZED name match
    against the pool (trim / collapse whitespace / case — the old exact
    ilike silently dropped "Row 2 " with a trailing space). Non-admission
    rows (drink coupons etc.) never count anywhere.
    """
    from sqlalchemy import func as sa_func

    from app.services.seating import normalized_name

    stamped = (
        db.query(sa_func.coalesce(sa_func.sum(Sale.quantity), 0))
        .filter(
            Sale.event_id == category.event_id,
            Sale.seating_category_id == category.id,
            Sale.is_admission.is_(True),
            Sale.all_days.is_(False),  # packages counted family-wide below, never here
            Sale.source != SaleSource.NATIVE,
        )
        .scalar()
        or 0
    )
    # 0050: imported packages (all_days) consume a head EVERY night.
    # They're stamped once, to the family's base pool; count them into
    # THIS pool whenever it belongs to the same name family — base or
    # any "(MM/DD)" sibling. A lone pool is its own family of one.
    from app.models.seating_category import SeatingCategory
    from app.services.seating import POOL_DAY_SUFFIX

    def _family_key(name):
        m = POOL_DAY_SUFFIX.search(str(name or ""))
        return normalized_name((name or "")[: m.start()] if m else name)

    my_family = _family_key(category.name)
    family_ids = [
        p.id
        for p in db.query(SeatingCategory).filter(SeatingCategory.event_id == category.event_id).all()
        if _family_key(p.name) == my_family
    ]
    packages = (
        db.query(sa_func.coalesce(sa_func.sum(Sale.quantity), 0))
        .filter(
            Sale.event_id == category.event_id,
            Sale.seating_category_id.in_(family_ids),
            Sale.all_days.is_(True),
            Sale.is_admission.is_(True),
            Sale.source != SaleSource.NATIVE,
        )
        .scalar()
        or 0
    )
    legacy = (
        db.query(sa_func.coalesce(sa_func.sum(Sale.quantity), 0))
        .filter(
            Sale.event_id == category.event_id,
            Sale.seating_category_id.is_(None),
            Sale.is_admission.is_(True),
            Sale.source != SaleSource.NATIVE,
            Sale.ticket_type.isnot(None),
            sa_func.btrim(sa_func.regexp_replace(sa_func.lower(Sale.ticket_type), r"\s+", " ", "g"))
            == normalized_name(category.name),
        )
        .scalar()
        or 0
    )
    return int(stamped) + int(packages) + int(legacy)


def sale_aggregates_by_code(db: Session, event_id: str) -> dict:
    """
    Per-promo-code rollup of the shared Sale table: transactions,
    tickets (SUM of quantity), dollars (SUM of amount over rows that
    have one), rows missing an amount, and accrued reward (SUM of
    computed_reward). Feeds BOTH the organizer's /promo-stats and the
    referrer portal — one function, so the number a referrer sees is
    the number the organizer sees, structurally.
    """
    rows = (
        db.query(
            Sale.promo_code_id.label("pcid"),
            func.count(Sale.id).label("sale_count"),
            func.coalesce(func.sum(Sale.quantity), 0).label("tickets_sold"),
            func.coalesce(func.sum(Sale.amount), 0).label("amount_sold"),
            func.count(Sale.id).filter(Sale.amount.is_(None)).label("rows_missing_amount"),
            func.sum(Sale.computed_reward).label("total_reward"),
            func.max(Sale.imported_at).label("last_sale_at"),
        )
        .filter(Sale.event_id == event_id, Sale.promo_code_id.isnot(None))
        .group_by(Sale.promo_code_id)
        .all()
    )
    return {r.pcid: r for r in rows}