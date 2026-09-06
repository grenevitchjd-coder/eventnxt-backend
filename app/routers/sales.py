# eventnxt-backend: app/routers/sales.py
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
import secrets

from app.models.event_profile import EventProfile
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
    SaleTypeMappingItem,
    SaleTypeMappingsPutRequest,
    SaleTypeMappingResponse,
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
    PromoStatRow,
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
from app.services.comp_tickets import event_days_for
from app.services.seating import normalized_name
from app.models.seating_category import SeatingCategory
from app.services import sale_matching
from app.services import sales as sales_service
from app.services import email as email_service
from app.services.deps import CurrentUser
from app.services.permissions import require_promotion, require_money

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
        reward_type=code.reward_type.value if code.reward_type is not None else None,
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
    event_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(require_money)
):
    config = db.query(SalesConfig).filter(SalesConfig.event_id == event_id).first()
    platform = config.platform.value if config else SalesPlatform.CUSTOM_CSV.value
    return SalesConfigResponse(platform=platform, available_platforms=sales_service.platform_options())


@router.put("/sales-config", response_model=SalesConfigResponse)
def set_sales_config(
    event_id: str,
    payload: SalesConfigUpdateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_money),
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
    user: CurrentUser = Depends(require_promotion),
):
    # Two kinds of code (0042): guest_id set = referral code (reward
    # terms required, as always); guest_id None = SELF PROMO — the org's
    # own marketing code, which must NOT carry reward machinery.
    if payload.guest_id is not None:
        guest = db.query(Guest).filter(Guest.id == payload.guest_id, Guest.event_id == event_id).first()
        if not guest:
            raise HTTPException(status_code=404, detail="Guest not found for this event.")
        if payload.reward_type is None:
            raise HTTPException(status_code=400, detail="A referral code needs a reward_type.")
    else:
        if payload.reward_type is not None or payload.points_rates:
            raise HTTPException(
                status_code=400,
                detail="A self promo (no referrer) can't have reward terms — create it from Referral Setup if someone should earn from it.",
            )

    existing = (
        db.query(PromoCode)
        .filter(PromoCode.event_id == event_id, PromoCode.code.ilike(payload.code))
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail=f'The code "{payload.code}" is already in use for this event.')

    if payload.guest_id is not None:
        _validate_reward_fields(payload.reward_type, payload.reward_value, payload.points_rates)
    elif payload.reward_value is not None:
        raise HTTPException(status_code=400, detail="A self promo can't have a reward_value.")
    _validate_discount_fields(payload.discount_type, payload.discount_value)

    code = PromoCode(
        event_id=event_id,
        guest_id=payload.guest_id,
        code=payload.code,
        reward_type=RewardType(payload.reward_type) if payload.reward_type is not None else None,
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
    event_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(require_promotion)
):
    codes = db.query(PromoCode).filter(PromoCode.event_id == event_id).all()
    return [_serialize_promo_code(db, c) for c in codes]


@router.patch("/promo-codes/{code_id}", response_model=PromoCodeResponse)
def update_promo_code(
    event_id: str,
    code_id: str,
    payload: PromoCodeUpdateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_promotion),
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

    # A code's kind never changes on edit — a self promo stays reward-less,
    # a referral code keeps requiring reward terms.
    if code.guest_id is not None:
        if payload.reward_type is None:
            raise HTTPException(status_code=400, detail="A referral code needs a reward_type.")
        _validate_reward_fields(payload.reward_type, payload.reward_value, payload.points_rates)
    else:
        if payload.reward_type is not None or payload.reward_value is not None or payload.points_rates:
            raise HTTPException(status_code=400, detail="A self promo (no referrer) can't have reward terms.")
    _validate_discount_fields(payload.discount_type, payload.discount_value)

    code.code = payload.code
    code.reward_type = RewardType(payload.reward_type) if payload.reward_type is not None else None
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
    user: CurrentUser = Depends(require_promotion),
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


# ---------- Referrers (Referral Setup page) ----------


class ReferrerCreateRequest(BaseModel):
    name: str
    email: EmailStr


class ReferrerResponse(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    rsvp_token: str
    is_referrer_only: bool


class SendReferrerPortalLinkRequest(BaseModel):
    portal_base_url: str  # frontend origin — link = {base}/referrer/{token}


@router.post("/referrers", response_model=ReferrerResponse, status_code=201)
def create_referrer(
    event_id: str,
    payload: ReferrerCreateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_promotion),
):
    """
    A referral person from scratch — an influencer or salesperson who
    may never attend. Still a Guest row (referrers always were), but
    is_referrer_only fences them out of every attendee flow: no offering
    type, no grants, nothing mints, no invite emails, absent from the
    door roster. Their deal is the promo code(s) attached next.
    """
    existing = (
        db.query(Guest)
        .filter(Guest.event_id == event_id, Guest.email.ilike(payload.email))
        .first()
    )
    if existing:
        # An attendee can also refer — codes just attach to their
        # existing row. Only a same-email REFERRER row is a duplicate.
        if existing.is_referrer_only:
            raise HTTPException(status_code=400, detail=f'"{payload.email}" is already a referrer for this event.')
        return ReferrerResponse(
            id=existing.id, name=existing.name, email=existing.email,
            rsvp_token=existing.rsvp_token, is_referrer_only=False,
        )
    guest = Guest(
        event_id=event_id,
        name=payload.name,
        email=payload.email,
        guest_type_id=None,
        is_referrer_only=True,
        rsvp_token=secrets.token_urlsafe(24),
    )
    db.add(guest)
    db.commit()
    db.refresh(guest)
    return ReferrerResponse(
        id=guest.id, name=guest.name, email=guest.email,
        rsvp_token=guest.rsvp_token, is_referrer_only=True,
    )


@router.post("/referrers/{guest_id}/send-portal-link")
def send_referrer_portal_link(
    event_id: str,
    guest_id: str,
    payload: SendReferrerPortalLinkRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_promotion),
):
    """
    Email a referrer their portal link (dashboard + reward claiming) and
    every share link they hold. Fired automatically by Referral Setup
    the moment a person is added with their first code, and available
    per-row for re-sends. Works for attendee-referrers too — the portal
    shows the referral side of whoever holds the token.
    """
    guest = db.query(Guest).filter(Guest.id == guest_id, Guest.event_id == event_id).first()
    if not guest:
        raise HTTPException(status_code=404, detail="Referrer not found.")
    codes = db.query(PromoCode).filter(PromoCode.event_id == event_id, PromoCode.guest_id == guest.id).all()
    if not codes:
        raise HTTPException(status_code=400, detail="Add this person's promo code first — the email includes their share links.")

    base = payload.portal_base_url.rstrip("/")
    portal_link = f"{base}/referrer/{guest.rsvp_token}"
    profile = db.query(EventProfile).filter(EventProfile.event_id == event_id).first()
    event_name = (profile.title if profile else None) or "the event"

    # 0052: NOTHING referral-facing ships before the payout-terms wall is
    # signed — the pre-signature email carries no codes or share links,
    # only the portal link (the door to the wall).
    if guest.payout_terms_accepted_at is None:
        text = "\n".join([
            f"Hi {guest.name},",
            "",
            f"You've been invited to be a referrer for {event_name}.",
            "",
            "Before you receive your unique referral code and share link, please",
            "review and accept the Referral Program Terms (your payout terms and",
            "the outreach rules) — it takes a minute:",
            "",
            f"  {portal_link}",
            "",
            "Once you've accepted, your code, share link, and live sales tracking",
            "unlock right there.",
            f"Full terms: {base}/terms/referral",
        ])
        try:
            email_service.send_email(to=guest.email, subject=f"Action needed: accept your referral terms for {event_name}", text_body=text)
        except Exception:
            raise HTTPException(status_code=400, detail="Email didn't send — check the event's email settings (SMTP) and the address.")
        return {"sent": True}

    lines = [f"Hi {guest.name},", "", f"You're set up as a referrer for {event_name}. Your code(s):", ""]
    for code in codes:
        deal = []
        if code.discount_type == "percentage":
            deal.append(f"buyers get {code.discount_value}% off")
        elif code.discount_type == "flat_amount":
            deal.append(f"buyers get ${code.discount_value} off")
        line = f"  {code.code}" + (f" — {', '.join(deal)}" if deal else "")
        lines.append(line)
        if profile and profile.is_published:
            lines.append(f"  Share link: {base}/e/{profile.slug}?ref={code.code}")
        lines.append("")
    if not (profile and profile.is_published):
        lines.append("(Share links will be in your portal once the event page is published.)")
        lines.append("")
    lines.append(f"Track your sales and claim rewards any time: {portal_link}")
    lines += [
        "",
        "About your payout: the reward terms for each of your codes are shown on",
        "your portal, and rewards accrue at the terms in effect when each sale",
        "happens. The organizer may adjust terms for FUTURE sales, so later",
        "payouts won't necessarily match your initial terms — your portal always",
        "shows the current effective terms, and volume bonuses you've already",
        "crossed are final. Payouts are settled after the event, net of refunds.",
        f"Full referral terms & outreach policy: {base}/terms/referral",
    ]
    text = "\n".join(lines)

    try:
        email_service.send_email(to=guest.email, subject=f"Your referral link for {event_name}", text_body=text)
    except Exception:
        raise HTTPException(status_code=400, detail="Email didn't send — check the event's email settings (SMTP) and the address.")
    return {"sent": True}


# ---------- Sales ----------


@router.get("/sales", response_model=list[SaleResponse])
def list_sales(event_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(require_money)):
    return db.query(Sale).filter(Sale.event_id == event_id).order_by(Sale.imported_at.desc()).all()


@router.get("/promo-stats", response_model=list[PromoStatRow])
def promo_stats(event_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(require_money)):
    """
    Per-code performance for the Promo Tracking and Referral Payouts
    pages: transactions, tickets (SUM of quantity — a bulk row counts
    all its heads), and dollars, aggregated from the shared Sale table —
    so native orders and CSV imports count identically, and the numbers
    here can never disagree with the Sales list below them. Codes with
    zero sales still appear (a promo that moved nothing is exactly what
    this page exists to reveal). Refunded orders' Sale rows are not
    reversed today, matching sale_count everywhere else — if refund
    netting ever lands, it lands in the Sale table and this page follows
    for free.
    """
    # Shared with the referrer portal (sale_aggregates_by_code) — one
    # function, one truth, org and referrer can never disagree.
    by_code = sales_service.sale_aggregates_by_code(db, event_id)

    rows = (
        db.query(PromoCode, Guest.name)
        .outerjoin(Guest, Guest.id == PromoCode.guest_id)
        .filter(PromoCode.event_id == event_id)
        .order_by(PromoCode.created_at)
        .all()
    )
    out = []
    for code, referrer_name in rows:
        a = by_code.get(code.id)
        out.append(
            PromoStatRow(
                id=code.id,
                code=code.code,
                guest_id=code.guest_id,
                referrer_name=referrer_name,
                discount_type=code.discount_type,
                discount_value=code.discount_value,
                link_clicks=code.link_clicks,
                sale_count=a.sale_count if a else 0,
                tickets_sold=int(a.tickets_sold) if a else 0,
                amount_sold=a.amount_sold if a else 0,
                rows_missing_amount=a.rows_missing_amount if a else 0,
                last_sale_at=a.last_sale_at if a else None,
            )
        )
    return out


@router.get("/sales/type-mappings", response_model=list[SaleTypeMappingResponse])
def list_sale_type_mappings(
    event_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(require_money)
):
    from app.models.sale_type_mapping import SaleTypeMapping

    return (
        db.query(SaleTypeMapping)
        .filter(SaleTypeMapping.event_id == event_id)
        .order_by(SaleTypeMapping.raw_label)
        .all()
    )


@router.put("/sales/type-mappings", response_model=list[SaleTypeMappingResponse])
def put_sale_type_mappings(
    event_id: str,
    payload: SaleTypeMappingsPutRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_money),
):
    """
    Bulk upsert of the organizer's label->area answers from import
    staging (0049). Keyed by NORMALIZED label per event, so "Row 2 " and
    "row 2" are one mapping. Mappings only shape FUTURE imports —
    already-imported rows keep the stamp they got (re-uploading the same
    file re-stamps nothing thanks to barcode dedup; a corrective
    re-import means deleting the affected sales first, unchanged
    behavior).
    """
    from app.models.sale_type_mapping import SaleTypeMapping

    for item in payload.mappings:
        sale_matching.upsert_mapping(
            db, event_id, item.raw_label, item.seating_category_id, item.face_value_cents,
            item.is_admission, item.all_days
        )
    db.commit()
    return (
        db.query(SaleTypeMapping)
        .filter(SaleTypeMapping.event_id == event_id)
        .order_by(SaleTypeMapping.raw_label)
        .all()
    )


@router.post("/sales/import", response_model=SalesImportResult)
def import_sales(
    event_id: str,
    payload: SalesImportRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_money),
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

    # 0049: one shared match pass per row (coupon parse, mapping lookup,
    # day routing, stamp) — batch context fetched once.
    days = event_days_for(db, event_id)
    mappings = sale_matching.get_mappings(db, event_id)
    pools_by_norm = {
        normalized_name(p.name): p.id
        for p in db.query(SeatingCategory).filter(SeatingCategory.event_id == event_id).all()
    }

    imported = 0
    skipped_duplicates = 0
    unmatched_code_count = 0
    affected_code_ids = set()

    for row in payload.rows:
        if row.external_transaction_id and row.external_transaction_id in already_seen:
            skipped_duplicates += 1
            continue
        enriched = sale_matching.enrich_import_row(
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
                "event_day": row.event_day,
                "discount_text": row.discount_text,
            },
            days,
            mappings,
            pools_by_norm,
        )
        sale = sales_service.reconcile_sale_row(db, event_id, enriched)
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
    user: CurrentUser = Depends(require_promotion),
):
    tier = RedemptionTier(event_id=event_id, points_required=payload.points_required, label=payload.label)
    db.add(tier)
    db.commit()
    db.refresh(tier)
    return tier


@router.get("/redemption-tiers", response_model=list[RedemptionTierResponse])
def list_redemption_tiers(
    event_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(require_promotion)
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
    user: CurrentUser = Depends(require_promotion),
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
    user: CurrentUser = Depends(require_promotion),
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
    user: CurrentUser = Depends(require_promotion),
):
    if payload.cash_value is None and payload.ticket_value is None:
        raise HTTPException(status_code=400, detail="Set at least one of cash_value or ticket_value.")

    code = db.query(PromoCode).filter(PromoCode.id == code_id, PromoCode.event_id == event_id).first()
    if not code:
        raise HTTPException(status_code=404, detail="Promo code not found.")
    if code.guest_id is None:
        raise HTTPException(status_code=400, detail="A self promo has no referrer to redeem rewards — redemption options only apply to referral codes.")
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
    user: CurrentUser = Depends(require_promotion),
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
    event_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(require_money)
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
    user: CurrentUser = Depends(require_money),
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
    user: CurrentUser = Depends(require_promotion),
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
    event_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(require_promotion)
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
    user: CurrentUser = Depends(require_promotion),
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
    user: CurrentUser = Depends(require_promotion),
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
    user: CurrentUser = Depends(require_promotion),
):
    """Sets this code's OWN bonus tiers, overriding the event default
    entirely — including submitting an empty list, which means "no
    bonuses for this code," distinct from inheriting the default."""
    code = db.query(PromoCode).filter(PromoCode.id == code_id, PromoCode.event_id == event_id).first()
    if not code:
        raise HTTPException(status_code=404, detail="Promo code not found.")
    if code.guest_id is None:
        raise HTTPException(status_code=400, detail="A self promo has no referrer to earn bonuses — bonus tiers only apply to referral codes.")
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
    user: CurrentUser = Depends(require_promotion),
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