import uuid
from datetime import datetime
from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class PlatformOption(BaseModel):
    value: str
    label: str
    has_live_api: bool


class SalesConfigResponse(BaseModel):
    platform: str
    available_platforms: List[PlatformOption]


class SalesConfigUpdateRequest(BaseModel):
    platform: Literal["custom_csv", "eventbrite", "ticketmaster", "square", "stripe", "other"]


class PointsRateItem(BaseModel):
    ticket_type: str
    points: int = Field(ge=0)


class PromoCodeCreateRequest(BaseModel):
    guest_id: uuid.UUID
    code: str
    reward_type: Literal["flat_amount", "percentage", "free_tickets", "points"]
    # Required for flat_amount / percentage / free_tickets; ignored for
    # points (which uses points_rates instead) — enforced in the router,
    # since which fields are required depends on reward_type.
    reward_value: Optional[Decimal] = Field(default=None, ge=0)
    # Only meaningful when reward_type is "points" — one entry per ticket
    # type this code earns points for. Omit or leave empty for any other
    # reward_type.
    points_rates: Optional[List[PointsRateItem]] = None
    referral_message_draft: Optional[str] = None


class PromoCodeUpdateRequest(BaseModel):
    code: str
    reward_type: Literal["flat_amount", "percentage", "free_tickets", "points"]
    reward_value: Optional[Decimal] = Field(default=None, ge=0)
    points_rates: Optional[List[PointsRateItem]] = None
    referral_message_draft: Optional[str] = None


class PromoCodeResponse(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    guest_id: uuid.UUID
    code: str
    reward_type: str
    reward_value: Optional[Decimal] = None
    points_rates: List[PointsRateItem] = []
    referral_message_draft: Optional[str] = None
    created_at: datetime
    # Rollup — computed at read time from this code's attributed sales,
    # not stored columns. total_reward is in the code's own reward unit
    # (dollars for flat/percentage, a ticket count for free_tickets,
    # points for points) — every sale under one code shares reward_type,
    # so summing is always unit-consistent.
    sale_count: int = 0
    total_reward: Optional[Decimal] = None
    # Only meaningful for a points-type code — points_earned minus points
    # already spent on redemptions. None for any other reward_type.
    points_available: Optional[int] = None
    bonus_awards: List["BonusAwardItem"] = []
    bonus_tiers_overridden: bool = False

    class Config:
        from_attributes = True


class SaleResponse(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    promo_code_id: Optional[uuid.UUID] = None
    buyer_name: Optional[str] = None
    buyer_email: Optional[str] = None
    amount: Optional[Decimal] = None
    ticket_type: Optional[str] = None
    quantity: int = 1
    sale_date: Optional[str] = None
    external_transaction_id: Optional[str] = None
    source: str
    computed_reward: Optional[Decimal] = None
    imported_at: datetime

    class Config:
        from_attributes = True


class SalesImportRow(BaseModel):
    buyer_name: Optional[str] = None
    buyer_email: Optional[str] = None
    amount: Optional[Decimal] = None
    ticket_type: Optional[str] = None
    quantity: int = Field(default=1, ge=1)
    promo_code: Optional[str] = None
    sale_date: Optional[str] = None
    external_transaction_id: Optional[str] = None


class SalesImportRequest(BaseModel):
    rows: List[SalesImportRow]


class SalesImportResult(BaseModel):
    imported: int
    skipped_duplicates: int
    unmatched_code_count: int


class RedemptionTierCreateRequest(BaseModel):
    points_required: int = Field(ge=1)
    label: Optional[str] = None


class RedemptionTierResponse(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    points_required: int
    label: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class RedemptionOptionUpsertRequest(BaseModel):
    cash_value: Optional[Decimal] = Field(default=None, ge=0)
    ticket_value: Optional[int] = Field(default=None, ge=1)


class RedemptionOptionResponse(BaseModel):
    id: uuid.UUID
    promo_code_id: uuid.UUID
    redemption_tier_id: uuid.UUID
    cash_value: Optional[Decimal] = None
    ticket_value: Optional[int] = None
    tier_points_required: int
    tier_label: Optional[str] = None

    class Config:
        from_attributes = True


class RewardRedemptionResponse(BaseModel):
    id: uuid.UUID
    promo_code_id: uuid.UUID
    redemption_tier_id: uuid.UUID
    choice: str
    points_spent: int
    cash_value: Optional[Decimal] = None
    ticket_value: Optional[int] = None
    created_guest_id: Optional[uuid.UUID] = None
    payout_status: Optional[str] = None
    redeemed_at: datetime
    promo_code: Optional[str] = None
    referrer_name: Optional[str] = None

    class Config:
        from_attributes = True


class RedeemRequest(BaseModel):
    promo_code_id: uuid.UUID
    redemption_tier_id: uuid.UUID
    choice: Literal["cash", "ticket"]



class BonusTierItem(BaseModel):
    tickets_required: int = Field(ge=1)
    bonus_value: Decimal = Field(ge=0)


class BonusTierCreateRequest(BaseModel):
    tickets_required: int = Field(ge=1)
    bonus_value: Decimal = Field(ge=0)


class BonusTierResponse(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    tickets_required: int
    bonus_value: Decimal
    created_at: datetime

    class Config:
        from_attributes = True


class PromoCodeBonusTiersRequest(BaseModel):
    tiers: List[BonusTierItem]


class PromoCodeBonusTiersResponse(BaseModel):
    overridden: bool
    tiers: List[BonusTierItem]


class BonusAwardItem(BaseModel):
    tickets_required: int
    bonus_value: Decimal
    awarded_at: datetime


PromoCodeResponse.model_rebuild()