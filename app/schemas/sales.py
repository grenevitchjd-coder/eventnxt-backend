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


class PromoCodeCreateRequest(BaseModel):
    guest_id: uuid.UUID
    code: str
    reward_type: Literal["flat_amount", "percentage", "free_tickets"]
    reward_value: Decimal = Field(ge=0)
    referral_message_draft: Optional[str] = None


class PromoCodeUpdateRequest(BaseModel):
    code: str
    reward_type: Literal["flat_amount", "percentage", "free_tickets"]
    reward_value: Decimal = Field(ge=0)
    referral_message_draft: Optional[str] = None


class PromoCodeResponse(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    guest_id: uuid.UUID
    code: str
    reward_type: str
    reward_value: Decimal
    referral_message_draft: Optional[str] = None
    created_at: datetime
    # Rollup — computed at read time from this code's attributed sales,
    # not stored columns.
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
    promo_code: Optional[str] = None
    sale_date: Optional[str] = None
    external_transaction_id: Optional[str] = None


class SalesImportRequest(BaseModel):
    rows: List[SalesImportRow]


class SalesImportResult(BaseModel):
    imported: int
    skipped_duplicates: int
    unmatched_code_count: int