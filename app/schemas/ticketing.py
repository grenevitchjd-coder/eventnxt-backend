"""eventnxt-backend: app/schemas/ticketing.py"""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


# ---------- Ticket types (organizer-facing) ----------


class TicketTypeCreateOrUpdateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    price_cents: int = Field(ge=0)  # 0 is legal — a free/comp type that skips Stripe entirely
    quantity: int = Field(ge=0)
    max_per_order: int = Field(default=10, ge=1)
    admits: int = Field(default=1, ge=1)
    seating_category_id: Optional[uuid.UUID] = None
    # Multi-day (per_day / mixed spans): the day this type sells for.
    # None = whole event (only legal outside per_day span).
    valid_date: Optional[str] = None
    sales_start: Optional[datetime] = None
    sales_end: Optional[datetime] = None
    is_active: bool = True
    sort_order: int = 0


class TicketTypeAdminResponse(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    seating_category_id: Optional[uuid.UUID] = None
    name: str
    description: Optional[str] = None
    price_cents: int
    currency: str
    quantity: int
    max_per_order: int
    admits: int = 1
    valid_date: Optional[str] = None  # multi-day: the day this type sells for (None = whole event)
    sales_start: Optional[datetime] = None
    sales_end: Optional[datetime] = None
    is_active: bool
    sort_order: int
    is_pass: bool = False  # derived all-days pass (has member nights)
    # Computed, not stored — see services/ticketing.py availability math.
    sold: int = 0
    held: int = 0
    comp_held: int = 0  # heads promised to comp guests on this type's pool (day-aware)
    available: int = 0

    class Config:
        from_attributes = True


# ---------- Public ticket picker ----------


class PublicTicketTypeResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: Optional[str] = None
    price_cents: int
    currency: str
    max_per_order: int
    admits: int = 1
    valid_date: Optional[str] = None  # multi-day: which day this type sells for (None = whole event)
    available: int
    on_sale: bool
    # True when this type sells specific seats — the picker swaps the
    # quantity stepper for section + seat selection.
    assigned_seating: bool = False
    # True when the buyer must choose a section (sectioned, unassigned).
    section_required: bool = False
    # The choosable sections for section_required types, with live
    # remaining heads so the picker can show "Section C · 12 left".
    sections: list["PublicTicketSectionOption"] = []
    # Sectioned all-days passes: one entry per night, each with that
    # night's choosable sections — the picker renders one dropdown per
    # night (the buyer may sit somewhere new every show).
    pass_nights: list["PublicPassNight"] = []


class PublicTicketSectionOption(BaseModel):
    id: uuid.UUID
    section_label: str
    row_label: Optional[str] = None
    remaining: int = 0  # active AND inside the sales window AND available > 0


class PublicPassNight(BaseModel):
    date: Optional[str] = None  # the member night's ISO day
    sections: list[PublicTicketSectionOption] = []


# ---------- Checkout ----------


class CheckoutItemRequest(BaseModel):
    ticket_type_id: uuid.UUID
    quantity: int = Field(ge=1)
    # Assigned-seat ticket types: the buyer's chosen seats (one per
    # quantity). Omit for other types.
    seat_ids: Optional[list[uuid.UUID]] = None
    # Sectioned unassigned types (rows/tables with a breakdown): the
    # buyer's chosen section — REQUIRED for those types.
    zone_section_id: Optional[uuid.UUID] = None
    # Sectioned all-days passes: the buyer's chosen section for EACH
    # night (they may differ — a new view every show). One id per night,
    # any order; each must belong to a different member night's pool.
    zone_section_ids: Optional[list[uuid.UUID]] = None


class CheckoutRequest(BaseModel):
    buyer_name: str
    buyer_email: EmailStr
    items: list[CheckoutItemRequest] = Field(min_length=1)
    # Optional referral/promo code — validated BEFORE any payment starts,
    # so attribution is known at purchase (unlike CSV imports, which match
    # after the fact). An unrecognized code is a 400, not a silent skip:
    # an interactive buyer deserves the chance to fix a typo.
    promo_code: Optional[str] = None


class CheckoutResponse(BaseModel):
    """
    For a paid order: checkout_url is the Stripe page to redirect to.
    For a $0 (comp/free) order: checkout_url is null and the order is
    ALREADY paid — the frontend goes straight to the order page.
    Either way, order_token is the buyer's permanent self-serve key.
    """

    order_token: str
    checkout_url: Optional[str] = None
    total_cents: int
    status: str


class PublicPromoCodeCheckResponse(BaseModel):
    """Buyer-facing only: says whether a code exists and what IT saves
    THEM — never the referrer's reward terms."""

    valid: bool
    discount_type: Optional[str] = None  # 'percentage' | 'flat_amount' | None (attribution-only code)
    discount_value: Optional[float] = None


class FindMyTicketsRequest(BaseModel):
    email: EmailStr


# ---------- Order retrieval (public, by token) ----------


class PublicOrderItemResponse(BaseModel):
    ticket_type_name: str
    quantity: int
    section_label: Optional[str] = None
    unit_price_cents: int


class PublicTicketResponse(BaseModel):
    code: str
    ticket_type_name: str
    status: str
    seat_label: Optional[str] = None  # "Section A · Row 1 · Seat 14"
    valid_date: Optional[str] = None  # multi-day: the day this code admits on


class PublicOrderResponse(BaseModel):
    status: str
    event_slug: str
    event_title: str
    buyer_name: str
    buyer_email: str
    currency: str
    subtotal_cents: int
    discount_cents: int = 0
    items: list[PublicOrderItemResponse] = []
    tickets: list[PublicTicketResponse] = []
    paid_at: Optional[datetime] = None
    refund_policy: Optional[str] = None


# ---------- Orders admin (organizer-facing) ----------


class AdminOrderItem(BaseModel):
    ticket_type_name: str
    quantity: int
    unit_price_cents: int


class AdminOrderResponse(BaseModel):
    id: uuid.UUID
    status: str
    buyer_name: str
    buyer_email: str
    currency: str
    subtotal_cents: int
    discount_cents: int
    platform_fee_cents: int
    organizer_net_cents: int
    order_token: str  # lets the admin open the buyer's order page directly
    items: list[AdminOrderItem] = []
    ticket_count: int = 0
    created_at: Optional[datetime] = None
    paid_at: Optional[datetime] = None
    refunded_at: Optional[datetime] = None

# ---------- Assigned-seat picker ----------


class PublicSeatResponse(BaseModel):
    id: uuid.UUID
    seat_number: int
    available: bool


class PublicSeatSectionResponse(BaseModel):
    section_label: str
    row_label: Optional[str] = None
    seats: list[PublicSeatResponse] = []


class PublicSeatMapResponse(BaseModel):
    ticket_type_id: uuid.UUID
    sections: list[PublicSeatSectionResponse] = []