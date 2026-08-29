import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.guest import Guest
from app.models.promo_code import PromoCode, RewardType
from app.models.promo_code_points_rate import PromoCodePointsRate
from app.models.sale import Sale
from app.models.sales_config import SalesConfig, SalesPlatform
from app.schemas.sales import (
    PointsRateItem,
    PromoCodeCreateRequest,
    PromoCodeResponse,
    PromoCodeUpdateRequest,
    SaleResponse,
    SalesConfigResponse,
    SalesConfigUpdateRequest,
    SalesImportRequest,
    SalesImportResult,
)
from app.services import sales as sales_service
from app.services.deps import CurrentUser
from app.services.event_access import require_event_access

router = APIRouter(prefix="/events/{event_id}", tags=["sales"])


def _serialize_promo_code(db: Session, code: PromoCode) -> PromoCodeResponse:
    code_sales = db.query(Sale).filter(Sale.promo_code_id == code.id).all()
    rewards = [s.computed_reward for s in code_sales if s.computed_reward is not None]
    total_reward = sum(rewards) if rewards else None
    rate_rows = db.query(PromoCodePointsRate).filter(PromoCodePointsRate.promo_code_id == code.id).all()
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
    )


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

    code = PromoCode(
        event_id=event_id,
        guest_id=payload.guest_id,
        code=payload.code,
        reward_type=RewardType(payload.reward_type),
        reward_value=payload.reward_value,
        referral_message_draft=payload.referral_message_draft,
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

    code.code = payload.code
    code.reward_type = RewardType(payload.reward_type)
    code.reward_value = payload.reward_value
    code.referral_message_draft = payload.referral_message_draft

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
                "promo_code": row.promo_code,
                "sale_date": row.sale_date,
                "external_transaction_id": row.external_transaction_id,
            },
        )
        if row.promo_code and sale.promo_code_id is None:
            unmatched_code_count += 1
        imported += 1

    db.commit()
    return SalesImportResult(
        imported=imported, skipped_duplicates=skipped_duplicates, unmatched_code_count=unmatched_code_count
    )