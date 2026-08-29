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
    promo_code: Optional[str] = None
    sale_date: Optional[str] = None
    external_transaction_id: Optional[str] = None


class SalesImportRequest(BaseModel):
    rows: List[SalesImportRow]


class SalesImportResult(BaseModel):
    imported: int
    skipped_duplicates: int
    unmatched_code_count: int