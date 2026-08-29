import secrets

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.bonus_award import BonusAward
from app.models.guest import Guest, GuestAllocationStatus
from app.models.promo_code import PromoCode
from app.models.promo_code_redemption_option import PromoCodeRedemptionOption
from app.models.redemption_tier import RedemptionTier
from app.models.reward_redemption import PayoutStatus, RedemptionChoice, RewardRedemption
from app.models.sale import Sale
from app.services import seating


def points_earned(db: Session, promo_code_id: str) -> int:
    """Total points a points-type code has earned — from attributed
    sales (the same aggregate as PromoCodeResponse.total_reward) PLUS
    any volume bonuses already awarded, since a bonus on a points-type
    code is itself denominated in points (see app/services/bonuses.py)
    and should count toward what's redeemable, same as sale-earned
    points."""
    from_sales = (
        db.query(func.coalesce(func.sum(Sale.computed_reward), 0))
        .filter(Sale.promo_code_id == promo_code_id)
        .scalar()
        or 0
    )
    from_bonuses = (
        db.query(func.coalesce(func.sum(BonusAward.bonus_value), 0))
        .filter(BonusAward.promo_code_id == promo_code_id)
        .scalar()
        or 0
    )
    return int(from_sales + from_bonuses)


def points_redeemed(db: Session, promo_code_id: str) -> int:
    total = (
        db.query(func.coalesce(func.sum(RewardRedemption.points_spent), 0))
        .filter(RewardRedemption.promo_code_id == promo_code_id)
        .scalar()
        or 0
    )
    return int(total)


def points_available(db: Session, promo_code_id: str) -> int:
    return points_earned(db, promo_code_id) - points_redeemed(db, promo_code_id)


def eligible_tiers(db: Session, event_id: str, promo_code_id: str):
    """
    Every redemption tier this specific code participates in (has an
    option configured for), each flagged with whether the code's current
    point balance can afford it. Tiers with no option row for this code
    are left out entirely — that code simply doesn't offer that tier.
    """
    available = points_available(db, promo_code_id)
    tiers = (
        db.query(RedemptionTier)
        .filter(RedemptionTier.event_id == event_id)
        .order_by(RedemptionTier.points_required)
        .all()
    )
    result = []
    for tier in tiers:
        option = (
            db.query(PromoCodeRedemptionOption)
            .filter(
                PromoCodeRedemptionOption.promo_code_id == promo_code_id,
                PromoCodeRedemptionOption.redemption_tier_id == tier.id,
            )
            .first()
        )
        if not option:
            continue
        result.append({"tier": tier, "option": option, "affordable": available >= tier.points_required})
    return available, result


def redeem(db: Session, event_id: str, promo_code_id: str, tier_id: str, choice: str) -> RewardRedemption:
    """
    The core redemption action. Row-locks the PromoCode for the rest of
    this transaction so two simultaneous redemption attempts against the
    same code's balance can't both succeed against points that only
    cover one of them — the second blocks here until the first commits,
    then re-reads the now-current balance. Only ever locks one promo_code
    row per transaction; different codes never contend with each other.

    A TICKET choice is fulfilled immediately in the same transaction —
    the referrer's own name/email, their own guest type, resolved
    through the exact same capacity-checked seating logic as any other
    guest. If the venue genuinely has no room left, the redemption fails
    outright rather than creating an unseated or overbooked guest — the
    referrer keeps their points and can try again or pick cash instead.
    A CASH choice can't be fulfilled by the app at all (no payment
    processing) — it's recorded as owed, PENDING, for the organizer to
    pay out and mark paid separately.
    """
    promo_code = db.query(PromoCode).filter(PromoCode.id == promo_code_id).with_for_update().first()
    if not promo_code:
        raise HTTPException(status_code=404, detail="Promo code not found.")

    tier = db.query(RedemptionTier).filter(RedemptionTier.id == tier_id, RedemptionTier.event_id == event_id).first()
    if not tier:
        raise HTTPException(status_code=404, detail="Redemption tier not found.")

    option = (
        db.query(PromoCodeRedemptionOption)
        .filter(
            PromoCodeRedemptionOption.promo_code_id == promo_code_id,
            PromoCodeRedemptionOption.redemption_tier_id == tier_id,
        )
        .first()
    )
    if not option:
        raise HTTPException(status_code=400, detail="This tier isn't available for this code.")

    if choice == "cash" and option.cash_value is None:
        raise HTTPException(status_code=400, detail="Cash isn't offered at this tier for this code.")
    if choice == "ticket" and option.ticket_value is None:
        raise HTTPException(status_code=400, detail="A ticket isn't offered at this tier for this code.")

    available = points_available(db, promo_code_id)  # read AFTER acquiring the lock — safe under concurrency
    if available < tier.points_required:
        raise HTTPException(
            status_code=400,
            detail=f"Only {available} point(s) available — this tier needs {tier.points_required}.",
        )

    redemption = RewardRedemption(
        promo_code_id=promo_code_id,
        redemption_tier_id=tier_id,
        choice=RedemptionChoice(choice),
        points_spent=tier.points_required,
        cash_value=option.cash_value if choice == "cash" else None,
        ticket_value=option.ticket_value if choice == "ticket" else None,
        payout_status=PayoutStatus.PENDING if choice == "cash" else None,
    )
    db.add(redemption)
    db.flush()  # assigns redemption.id

    if choice == "ticket":
        referrer = db.query(Guest).filter(Guest.id == promo_code.guest_id).first()
        new_category_id = seating.resolve_seating_from_priorities(
            db, event_id, str(referrer.guest_type_id), party_size=option.ticket_value
        )
        if new_category_id is None and seating.has_seating_priorities(db, str(referrer.guest_type_id)):
            raise HTTPException(status_code=400, detail="Sorry — there's no room left to fulfill this reward.")

        new_guest = Guest(
            event_id=event_id,
            name=f"{referrer.name} (referral reward)",
            email=referrer.email,
            guest_type_id=referrer.guest_type_id,
            seating_category_id=new_category_id,
            allocation_status=GuestAllocationStatus.CONFIRMED,
            party_size=option.ticket_value,
            rsvp_token=secrets.token_urlsafe(24),
        )
        db.add(new_guest)
        db.flush()
        redemption.created_guest_id = new_guest.id

    db.commit()
    db.refresh(redemption)
    return redemption