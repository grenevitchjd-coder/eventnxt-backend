from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.bonus_award import BonusAward
from app.models.event_bonus_tier import EventBonusTier
from app.models.promo_code import PromoCode
from app.models.promo_code_bonus_tier import PromoCodeBonusTier
from app.models.sale import Sale


def effective_bonus_tiers(db: Session, event_id: str, promo_code: PromoCode):
    """A code's own override rows if bonus_tiers_overridden is True
    (even if that's an empty list — meaning "no bonuses for this one"),
    otherwise the event's default tiers. Same resolution pattern as
    Guest ticket-allotment overrides."""
    if promo_code.bonus_tiers_overridden:
        return (
            db.query(PromoCodeBonusTier)
            .filter(PromoCodeBonusTier.promo_code_id == promo_code.id)
            .order_by(PromoCodeBonusTier.tickets_required)
            .all()
        )
    return (
        db.query(EventBonusTier)
        .filter(EventBonusTier.event_id == event_id)
        .order_by(EventBonusTier.tickets_required)
        .all()
    )


def replace_promo_code_bonus_tiers(db: Session, promo_code_id: str, items) -> None:
    """Wholesale replace a code's own bonus-tier override rows with
    `items` (objects with .tickets_required and .bonus_value). Does NOT
    touch bonus_tiers_overridden; the caller sets that."""
    db.query(PromoCodeBonusTier).filter(PromoCodeBonusTier.promo_code_id == promo_code_id).delete()
    for item in items:
        db.add(
            PromoCodeBonusTier(
                promo_code_id=promo_code_id, tickets_required=item.tickets_required, bonus_value=item.bonus_value
            )
        )


def total_bonus_awarded(db: Session, promo_code_id: str):
    """Sum of every bonus this code has actually been given — in the
    code's own reward unit (dollars, points, or tickets), since
    EventBonusTier/PromoCodeBonusTier bonus_value is always denominated
    in whatever unit the code's reward_type already uses."""
    total = (
        db.query(func.coalesce(func.sum(BonusAward.bonus_value), 0))
        .filter(BonusAward.promo_code_id == promo_code_id)
        .scalar()
        or 0
    )
    return total


def check_and_award_bonuses(db: Session, event_id: str, promo_code_id: str) -> list:
    """
    Call after new sales have been attributed to a code (e.g. at the end
    of a sales import). Locks the code's own row first — a bonus-check
    only ever touches one promo_code row, so unlike seating there's no
    multi-row lock-ordering concern, but the lock still matters: it
    serializes two overlapping imports for the same code so they can't
    both decide the same threshold hasn't been awarded yet and both try
    to award it.

    Each award attempt runs inside its own SAVEPOINT (db.begin_nested())
    rather than relying on the outer transaction — if the unique
    constraint backstop ever does fire (the lock should normally prevent
    that, but defense in depth), only that one award is discarded, not
    the whole batch of sales this function was called in the middle of
    reconciling. A plain db.rollback() here would be a real bug: it
    would wipe out every Sale row already added earlier in the same
    import, not just the one failed award.

    Returns the list of newly-created BonusAward rows (empty if nothing
    new was crossed). Does NOT commit — caller controls the transaction,
    same convention as reconcile_sale_row.
    """
    promo_code = db.query(PromoCode).filter(PromoCode.id == promo_code_id).with_for_update().first()
    if not promo_code:
        return []

    # This app's sessions run with autoflush=False, so a Sale row added
    # earlier in the same request (e.g. by reconcile_sale_row, just
    # before this function is called) isn't visible to the count query
    # below until explicitly flushed — without this, sale_count would
    # undercount by exactly the sales this same import batch just added.
    db.flush()

    tiers = effective_bonus_tiers(db, event_id, promo_code)
    if not tiers:
        return []

    sale_count = db.query(func.count(Sale.id)).filter(Sale.promo_code_id == promo_code_id).scalar() or 0

    already_awarded_thresholds = {
        row[0]
        for row in db.query(BonusAward.tickets_required).filter(BonusAward.promo_code_id == promo_code_id).all()
    }

    newly_awarded = []
    for tier in tiers:
        if sale_count < tier.tickets_required:
            continue
        if tier.tickets_required in already_awarded_thresholds:
            continue

        award = BonusAward(
            promo_code_id=promo_code_id,
            tickets_required=tier.tickets_required,
            bonus_value=tier.bonus_value,
        )
        try:
            with db.begin_nested():  # SAVEPOINT — isolates just this insert
                db.add(award)
                db.flush()
        except IntegrityError:
            # Another concurrent process already awarded this exact
            # tier — the lock above should normally prevent this from
            # ever actually happening, but if it does, only this one
            # award is discarded; everything else in the outer
            # transaction is untouched.
            continue

        newly_awarded.append(award)
        already_awarded_thresholds.add(tier.tickets_required)

    return newly_awarded