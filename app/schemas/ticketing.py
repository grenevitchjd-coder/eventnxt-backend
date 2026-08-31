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
    sales_start: Optional[datetime] = None
    sales_end: Optional[datetime] = None
    is_active: bool
    sort_order: int
    # Computed, not stored — see services/ticketing.py availability math.
    sold: int = 0
    held: int = 0
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
    available: int
    on_sale: bool  # active AND inside the sales window AND available > 0


# ---------- Checkout ----------


class CheckoutItemRequest(BaseModel):
    ticket_type_id: uuid.UUID
    quantity: int = Field(ge=1)


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
    unit_price_cents: int


class PublicTicketResponse(BaseModel):
    code: str
    ticket_type_name: str
    status: str


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