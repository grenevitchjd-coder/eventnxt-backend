import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.guest import Guest
from app.models.bonus_award import BonusAward
from app.models.event_bonus_tier import EventBonusTier
from app.models.promo_code import PromoCode, RewardType
from app.models.promo_code_bonus_tier import PromoCodeBonusTier
from app.models.promo_code_points_rate import PromoCodePointsRate
from app.models.promo_code_redemption_option import PromoCodeRedemptionOption
from app.models.redemption_tier import RedemptionTier
from app.models.reward_redemption import PayoutStatus, RewardRedemption
from app.models.sale import Sale
from app.models.sales_config import SalesConfig, SalesPlatform
from app.schemas.sales import (
    BonusAwardItem,
    BonusTierCreateRequest,
    BonusTierItem,
    BonusTierResponse,
    PointsRateItem,
    PromoCodeBonusTiersRequest,
    PromoCodeBonusTiersResponse,
    PromoCodeCreateRequest,
    PromoCodeResponse,
    PromoCodeUpdateRequest,
    RedemptionOptionResponse,
    RedemptionOptionUpsertRequest,
    RedemptionTierCreateRequest,
    RedemptionTierResponse,
    RewardRedemptionResponse,
    SaleResponse,
    SalesConfigResponse,
    SalesConfigUpdateRequest,
    SalesImportRequest,
    SalesImportResult,
)
from app.services import bonuses as bonuses_service
from app.services import redemptions as redemptions_service
from app.services import sales as sales_service
from app.services.deps import CurrentUser
from app.services.event_access import require_event_access

router = APIRouter(prefix="/events/{event_id}", tags=["sales"])


def _serialize_promo_code(db: Session, code: PromoCode) -> PromoCodeResponse:
    code_sales = db.query(Sale).filter(Sale.promo_code_id == code.id).all()
    rewards = [s.computed_reward for s in code_sales if s.computed_reward is not None]
    total_reward = sum(rewards) if rewards else None
    rate_rows = db.query(PromoCodePointsRate).filter(PromoCodePointsRate.promo_code_id == code.id).all()
    points_available = (
        redemptions_service.points_available(db, code.id) if code.reward_type == RewardType.POINTS else None
    )
    award_rows = (
        db.query(BonusAward).filter(BonusAward.promo_code_id == code.id).order_by(BonusAward.awarded_at).all()
    )
    return PromoCodeResponse(
        id=code.id,
        event_id=code.event_id,
        guest_id=code.guest_id,
        code=code.code,
        reward_type=code.reward_type.value,
        reward_value=code.reward_value,
        points_rates=[PointsRateItem(ticket_type=r.ticket_type, points=r.points) for r in rate_rows],
        referral_message_draft=code.referral_message_draft,
        created_at=code.created_at,
        sale_count=len(code_sales),
        total_reward=total_reward,
        points_available=points_available,
        bonus_awards=[
            BonusAwardItem(tickets_required=a.tickets_required, bonus_value=a.bonus_value, awarded_at=a.awarded_at)
            for a in award_rows
        ],
        bonus_tiers_overridden=code.bonus_tiers_overridden,
    )


def _validate_discount_fields(discount_type, discount_value):
    """Both-or-neither; percentage capped at 100. Money-shaped input gets checked at the door."""
    if discount_type is None and discount_value is None:
        return
    if discount_type is None or discount_value is None:
        raise HTTPException(status_code=400, detail="Discount needs both a type and a value (or neither).")
    if discount_type == "percentage" and discount_value > 100:
        raise HTTPException(status_code=400, detail="A percentage discount can't exceed 100.")


def _validate_reward_fields(reward_type: str, reward_value, points_rates):
    """
    Which fields are required depends on reward_type — enforced here
    rather than in the schema, since Pydantic alone can't express
    "reward_value is required unless reward_type is points."
    """
    if reward_type == "points":
        if reward_value is not None:
            raise HTTPException(status_code=400, detail="reward_value isn't used for a points code — use points_rates instead.")
    else:
        if reward_value is None:
            raise HTTPException(status_code=400, detail=f'reward_value is required for a "{reward_type}" code.')
        if points_rates:
            raise HTTPException(
                status_code=400, detail="points_rates only applies to a points-type code."
            )


# ---------- Sales platform setup ----------


@router.get("/sales-config", response_model=SalesConfigResponse)
def get_sales_config(
    event_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(require_event_access)
):
    config = db.query(SalesConfig).filter(SalesConfig.event_id == event_id).first()
    platform = config.platform.value if config else SalesPlatform.CUSTOM_CSV.value
    return SalesConfigResponse(platform=platform, available_platforms=sales_service.platform_options())


@router.put("/sales-config", response_model=SalesConfigResponse)
def set_sales_config(
    event_id: str,
    payload: SalesConfigUpdateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    config = db.query(SalesConfig).filter(SalesConfig.event_id == event_id).first()
    if config:
        config.platform = SalesPlatform(payload.platform)
    else:
        config = SalesConfig(event_id=event_id, platform=SalesPlatform(payload.platform))
        db.add(config)
    db.commit()
    return SalesConfigResponse(platform=payload.platform, available_platforms=sales_service.platform_options())


# ---------- Promo codes ----------


@router.post("/promo-codes", response_model=PromoCodeResponse, status_code=201)
def create_promo_code(
    event_id: str,
    payload: PromoCodeCreateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    guest = db.query(Guest).filter(Guest.id == payload.guest_id, Guest.event_id == event_id).first()
    if not guest:
        raise HTTPException(status_code=404, detail="Guest not found for this event.")

    existing = (
        db.query(PromoCode)
        .filter(PromoCode.event_id == event_id, PromoCode.code.ilike(payload.code))
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail=f'The code "{payload.code}" is already in use for this event.')

    _validate_reward_fields(payload.reward_type, payload.reward_value, payload.points_rates)
    _validate_discount_fields(payload.discount_type, payload.discount_value)

    code = PromoCode(
        event_id=event_id,
        guest_id=payload.guest_id,
        code=payload.code,
        reward_type=RewardType(payload.reward_type),
        reward_value=payload.reward_value,
        referral_message_draft=payload.referral_message_draft,
        discount_type=payload.discount_type,
        discount_value=payload.discount_value,
    )
    db.add(code)
    db.flush()  # assigns code.id without committing, needed for the FK below

    if payload.reward_type == "points" and payload.points_rates:
        sales_service.replace_points_rates(db, code.id, payload.points_rates)

    db.commit()
    db.refresh(code)
    return _serialize_promo_code(db, code)


@router.get("/promo-codes", response_model=list[PromoCodeResponse])
def list_promo_codes(
    event_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(require_event_access)
):
    codes = db.query(PromoCode).filter(PromoCode.event_id == event_id).all()
    return [_serialize_promo_code(db, c) for c in codes]


@router.patch("/promo-codes/{code_id}", response_model=PromoCodeResponse)
def update_promo_code(
    event_id: str,
    code_id: str,
    payload: PromoCodeUpdateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    code = db.query(PromoCode).filter(PromoCode.id == code_id, PromoCode.event_id == event_id).first()
    if not code:
        raise HTTPException(status_code=404, detail="Promo code not found.")

    if payload.code.lower() != code.code.lower():
        existing = (
            db.query(PromoCode)
            .filter(PromoCode.event_id == event_id, PromoCode.code.ilike(payload.code), PromoCode.id != code_id)
            .first()
        )
        if existing:
            raise HTTPException(
                status_code=400, detail=f'The code "{payload.code}" is already in use for this event.'
            )

    _validate_reward_fields(payload.reward_type, payload.reward_value, payload.points_rates)
    _validate_discount_fields(payload.discount_type, payload.discount_value)

    code.code = payload.code
    code.reward_type = RewardType(payload.reward_type)
    code.reward_value = payload.reward_value
    code.referral_message_draft = payload.referral_message_draft
    code.discount_type = payload.discount_type
    code.discount_value = payload.discount_value

    if payload.reward_type == "points":
        sales_service.replace_points_rates(db, code.id, payload.points_rates or [])

    db.commit()
    db.refresh(code)
    return _serialize_promo_code(db, code)


@router.delete("/promo-codes/{code_id}", status_code=204)
def delete_promo_code(
    event_id: str,
    code_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    code = db.query(PromoCode).filter(PromoCode.id == code_id, PromoCode.event_id == event_id).first()
    if not code:
        raise HTTPException(status_code=404, detail="Promo code not found.")
    sale_count = db.query(Sale).filter(Sale.promo_code_id == code_id).count()
    if sale_count > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Can't delete — {sale_count} sale(s) are already attributed to this code.",
        )
    db.query(PromoCodePointsRate).filter(PromoCodePointsRate.promo_code_id == code_id).delete()
    db.query(PromoCodeBonusTier).filter(PromoCodeBonusTier.promo_code_id == code_id).delete()
    db.delete(code)
    db.commit()


# ---------- Sales ----------


@router.get("/sales", response_model=list[SaleResponse])
def list_sales(event_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(require_event_access)):
    return db.query(Sale).filter(Sale.event_id == event_id).order_by(Sale.imported_at.desc()).all()


@router.post("/sales/import", response_model=SalesImportResult)
def import_sales(
    event_id: str,
    payload: SalesImportRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    """
    Reconciles a batch of sales rows (parsed client-side from a CSV/Excel
    upload, same pattern as the guest importer) against this event's
    promo codes. Rows sharing an external_transaction_id with a sale
    already on file are skipped — protects against double-counting when
    a box office export is a full historical snapshot rather than
    only-new-rows.
    """
    incoming_ids = [r.external_transaction_id for r in payload.rows if r.external_transaction_id]
    already_seen = sales_service.existing_transaction_ids(db, event_id, incoming_ids)

    imported = 0
    skipped_duplicates = 0
    unmatched_code_count = 0
    affected_code_ids = set()

    for row in payload.rows:
        if row.external_transaction_id and row.external_transaction_id in already_seen:
            skipped_duplicates += 1
            continue
        sale = sales_service.reconcile_sale_row(
            db,
            event_id,
            {
                "buyer_name": row.buyer_name,
                "buyer_email": row.buyer_email,
                "amount": row.amount,
                "ticket_type": row.ticket_type,
                "quantity": row.quantity,
                "promo_code": row.promo_code,
                "sale_date": row.sale_date,
                "external_transaction_id": row.external_transaction_id,
            },
        )
        if row.promo_code and sale.promo_code_id is None:
            unmatched_code_count += 1
        if sale.promo_code_id is not None:
            affected_code_ids.add(str(sale.promo_code_id))
        imported += 1

    # Volume bonus tiers are checked once per code that received new
    # sales in this batch, not per row — a threshold is about the
    # cumulative count, so it only needs evaluating after all of this
    # batch's sales for that code are in.
    for code_id in affected_code_ids:
        bonuses_service.check_and_award_bonuses(db, event_id, code_id)

    db.commit()
    return SalesImportResult(
        imported=imported, skipped_duplicates=skipped_duplicates, unmatched_code_count=unmatched_code_count
    )


# ---------- Redemption tiers (event-wide shared threshold structure) ----------


@router.post("/redemption-tiers", response_model=RedemptionTierResponse, status_code=201)
def create_redemption_tier(
    event_id: str,
    payload: RedemptionTierCreateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    tier = RedemptionTier(event_id=event_id, points_required=payload.points_required, label=payload.label)
    db.add(tier)
    db.commit()
    db.refresh(tier)
    return tier


@router.get("/redemption-tiers", response_model=list[RedemptionTierResponse])
def list_redemption_tiers(
    event_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(require_event_access)
):
    return (
        db.query(RedemptionTier)
        .filter(RedemptionTier.event_id == event_id)
        .order_by(RedemptionTier.points_required)
        .all()
    )


@router.delete("/redemption-tiers/{tier_id}", status_code=204)
def delete_redemption_tier(
    event_id: str,
    tier_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    tier = db.query(RedemptionTier).filter(RedemptionTier.id == tier_id, RedemptionTier.event_id == event_id).first()
    if not tier:
        raise HTTPException(status_code=404, detail="Redemption tier not found.")
    redemption_count = db.query(RewardRedemption).filter(RewardRedemption.redemption_tier_id == tier_id).count()
    if redemption_count > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Can't delete — {redemption_count} redemption(s) have already used this tier.",
        )
    db.query(PromoCodeRedemptionOption).filter(PromoCodeRedemptionOption.redemption_tier_id == tier_id).delete()
    db.delete(tier)
    db.commit()


# ---------- Per-code redemption options (what THIS code offers at a shared tier) ----------


@router.get("/promo-codes/{code_id}/redemption-options", response_model=list[RedemptionOptionResponse])
def list_redemption_options(
    event_id: str,
    code_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    rows = (
        db.query(PromoCodeRedemptionOption, RedemptionTier)
        .join(RedemptionTier, PromoCodeRedemptionOption.redemption_tier_id == RedemptionTier.id)
        .filter(PromoCodeRedemptionOption.promo_code_id == code_id)
        .order_by(RedemptionTier.points_required)
        .all()
    )
    return [
        RedemptionOptionResponse(
            id=option.id,
            promo_code_id=option.promo_code_id,
            redemption_tier_id=option.redemption_tier_id,
            cash_value=option.cash_value,
            ticket_value=option.ticket_value,
            tier_points_required=tier.points_required,
            tier_label=tier.label,
        )
        for option, tier in rows
    ]


@router.put(
    "/promo-codes/{code_id}/redemption-options/{tier_id}", response_model=RedemptionOptionResponse
)
def upsert_redemption_option(
    event_id: str,
    code_id: str,
    tier_id: str,
    payload: RedemptionOptionUpsertRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    if payload.cash_value is None and payload.ticket_value is None:
        raise HTTPException(status_code=400, detail="Set at least one of cash_value or ticket_value.")

    code = db.query(PromoCode).filter(PromoCode.id == code_id, PromoCode.event_id == event_id).first()
    if not code:
        raise HTTPException(status_code=404, detail="Promo code not found.")
    tier = db.query(RedemptionTier).filter(RedemptionTier.id == tier_id, RedemptionTier.event_id == event_id).first()
    if not tier:
        raise HTTPException(status_code=404, detail="Redemption tier not found.")

    option = (
        db.query(PromoCodeRedemptionOption)
        .filter(
            PromoCodeRedemptionOption.promo_code_id == code_id,
            PromoCodeRedemptionOption.redemption_tier_id == tier_id,
        )
        .first()
    )
    if option:
        option.cash_value = payload.cash_value
        option.ticket_value = payload.ticket_value
    else:
        option = PromoCodeRedemptionOption(
            promo_code_id=code_id,
            redemption_tier_id=tier_id,
            cash_value=payload.cash_value,
            ticket_value=payload.ticket_value,
        )
        db.add(option)
    db.commit()
    db.refresh(option)
    return RedemptionOptionResponse(
        id=option.id,
        promo_code_id=option.promo_code_id,
        redemption_tier_id=option.redemption_tier_id,
        cash_value=option.cash_value,
        ticket_value=option.ticket_value,
        tier_points_required=tier.points_required,
        tier_label=tier.label,
    )


@router.delete("/promo-codes/{code_id}/redemption-options/{tier_id}", status_code=204)
def delete_redemption_option(
    event_id: str,
    code_id: str,
    tier_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    option = (
        db.query(PromoCodeRedemptionOption)
        .filter(
            PromoCodeRedemptionOption.promo_code_id == code_id,
            PromoCodeRedemptionOption.redemption_tier_id == tier_id,
        )
        .first()
    )
    if not option:
        raise HTTPException(status_code=404, detail="No option set for this code at this tier.")
    db.delete(option)
    db.commit()


# ---------- Organizer payout queue ----------


@router.get("/reward-redemptions", response_model=list[RewardRedemptionResponse])
def list_reward_redemptions(
    event_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(require_event_access)
):
    rows = (
        db.query(RewardRedemption, PromoCode, Guest)
        .join(PromoCode, RewardRedemption.promo_code_id == PromoCode.id)
        .join(Guest, PromoCode.guest_id == Guest.id)
        .filter(PromoCode.event_id == event_id)
        .order_by(RewardRedemption.redeemed_at.desc())
        .all()
    )
    return [
        RewardRedemptionResponse(
            id=redemption.id,
            promo_code_id=redemption.promo_code_id,
            redemption_tier_id=redemption.redemption_tier_id,
            choice=redemption.choice.value,
            points_spent=redemption.points_spent,
            cash_value=redemption.cash_value,
            ticket_value=redemption.ticket_value,
            created_guest_id=redemption.created_guest_id,
            payout_status=redemption.payout_status.value if redemption.payout_status else None,
            redeemed_at=redemption.redeemed_at,
            promo_code=code.code,
            referrer_name=guest.name,
        )
        for redemption, code, guest in rows
    ]


@router.patch("/reward-redemptions/{redemption_id}/mark-paid", response_model=RewardRedemptionResponse)
def mark_redemption_paid(
    event_id: str,
    redemption_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    row = (
        db.query(RewardRedemption, PromoCode, Guest)
        .join(PromoCode, RewardRedemption.promo_code_id == PromoCode.id)
        .join(Guest, PromoCode.guest_id == Guest.id)
        .filter(RewardRedemption.id == redemption_id, PromoCode.event_id == event_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Redemption not found.")
    redemption, code, guest = row
    if redemption.payout_status is None:
        raise HTTPException(status_code=400, detail="This redemption wasn't a cash payout.")
    redemption.payout_status = PayoutStatus.PAID
    db.commit()
    db.refresh(redemption)
    return RewardRedemptionResponse(
        id=redemption.id,
        promo_code_id=redemption.promo_code_id,
        redemption_tier_id=redemption.redemption_tier_id,
        choice=redemption.choice.value,
        points_spent=redemption.points_spent,
        cash_value=redemption.cash_value,
        ticket_value=redemption.ticket_value,
        created_guest_id=redemption.created_guest_id,
        payout_status=redemption.payout_status.value if redemption.payout_status else None,
        redeemed_at=redemption.redeemed_at,
        promo_code=code.code,
        referrer_name=guest.name,
    )


# ---------- Event-wide default bonus tiers ----------


@router.post("/bonus-tiers", response_model=BonusTierResponse, status_code=201)
def create_bonus_tier(
    event_id: str,
    payload: BonusTierCreateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    """
    The organizer's default volume-bonus structure — applies to every
    code that hasn't overridden it. Freely editable/deletable after
    creation, unlike redemption tiers: BonusAward snapshots its own
    tickets_required/bonus_value at award time rather than referencing
    this row, so changing or removing a tier here never rewrites a bonus
    that's already been given.
    """
    tier = EventBonusTier(event_id=event_id, tickets_required=payload.tickets_required, bonus_value=payload.bonus_value)
    db.add(tier)
    db.commit()
    db.refresh(tier)
    return tier


@router.get("/bonus-tiers", response_model=list[BonusTierResponse])
def list_bonus_tiers(
    event_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(require_event_access)
):
    return (
        db.query(EventBonusTier)
        .filter(EventBonusTier.event_id == event_id)
        .order_by(EventBonusTier.tickets_required)
        .all()
    )


@router.delete("/bonus-tiers/{tier_id}", status_code=204)
def delete_bonus_tier(
    event_id: str,
    tier_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    tier = db.query(EventBonusTier).filter(EventBonusTier.id == tier_id, EventBonusTier.event_id == event_id).first()
    if not tier:
        raise HTTPException(status_code=404, detail="Bonus tier not found.")
    db.delete(tier)
    db.commit()


# ---------- Per-code bonus tier override ----------


@router.get("/promo-codes/{code_id}/bonus-tiers", response_model=PromoCodeBonusTiersResponse)
def get_promo_code_bonus_tiers(
    event_id: str,
    code_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    code = db.query(PromoCode).filter(PromoCode.id == code_id, PromoCode.event_id == event_id).first()
    if not code:
        raise HTTPException(status_code=404, detail="Promo code not found.")
    tiers = bonuses_service.effective_bonus_tiers(db, event_id, code)
    return PromoCodeBonusTiersResponse(
        overridden=code.bonus_tiers_overridden,
        tiers=[BonusTierItem(tickets_required=t.tickets_required, bonus_value=t.bonus_value) for t in tiers],
    )


@router.put("/promo-codes/{code_id}/bonus-tiers", response_model=PromoCodeBonusTiersResponse)
def set_promo_code_bonus_tiers(
    event_id: str,
    code_id: str,
    payload: PromoCodeBonusTiersRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    """Sets this code's OWN bonus tiers, overriding the event default
    entirely — including submitting an empty list, which means "no
    bonuses for this code," distinct from inheriting the default."""
    code = db.query(PromoCode).filter(PromoCode.id == code_id, PromoCode.event_id == event_id).first()
    if not code:
        raise HTTPException(status_code=404, detail="Promo code not found.")
    code.bonus_tiers_overridden = True
    bonuses_service.replace_promo_code_bonus_tiers(db, code_id, payload.tiers)
    db.commit()
    tiers = bonuses_service.effective_bonus_tiers(db, event_id, code)
    return PromoCodeBonusTiersResponse(
        overridden=True,
        tiers=[BonusTierItem(tickets_required=t.tickets_required, bonus_value=t.bonus_value) for t in tiers],
    )


@router.delete("/promo-codes/{code_id}/bonus-tiers", response_model=PromoCodeBonusTiersResponse)
def clear_promo_code_bonus_tiers(
    event_id: str,
    code_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    """Clears this code's override, reverting it back to inheriting the
    event's default bonus tiers."""
    code = db.query(PromoCode).filter(PromoCode.id == code_id, PromoCode.event_id == event_id).first()
    if not code:
        raise HTTPException(status_code=404, detail="Promo code not found.")
    code.bonus_tiers_overridden = False
    db.query(PromoCodeBonusTier).filter(PromoCodeBonusTier.promo_code_id == code_id).delete()
    db.commit()
    tiers = bonuses_service.effective_bonus_tiers(db, event_id, code)
    return PromoCodeBonusTiersResponse(
        overridden=False,
        tiers=[BonusTierItem(tickets_required=t.tickets_required, bonus_value=t.bonus_value) for t in tiers],
    )