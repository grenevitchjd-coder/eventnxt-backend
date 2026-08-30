# eventnxt-backend: app/schemas/rsvp.py
from typing import List, Literal, Optional

from pydantic import BaseModel, EmailStr, Field


class DayAllotment(BaseModel):
    """One day's pool for an allotment holder — its own separate total,
    distributed count, and remaining count. Never mixed with any other
    day's numbers."""

    date: str
    total: int
    distributed: int
    remaining: int


class RSVPInfoResponse(BaseModel):
    """
    What a guest sees when they open their own RSVP link. Two shapes in
    one response, distinguished by is_allotment_holder: a plain guest (or
    a delegated recipient) just confirms/declines for themselves; an
    allotment holder (model, sponsor) sees their per-day ticket pools and
    distributes them to others instead.
    """

    guest_name: str
    guest_type_name: str
    allocation_status: str
    visit_date: Optional[str] = None
    party_size: int

    is_allotment_holder: bool
    day_allotments: Optional[List[DayAllotment]] = None
    distributed_recipients: Optional[List["DistributedRecipient"]] = None

    # The guest's actual experience ('invite' | 'distribute' | 'select'),
    # so the page renders the right interaction without re-deriving it.
    effective_mode: str = "invite"
    # RSVP'd yes but seating couldn't resolve — page shows the soft
    # "your ticket will arrive once seating is finalized" message.
    needs_seating: bool = False
    # 'select' mode: the days this guest may choose from (guest-type
    # allotment days when defined; empty = free choice).
    available_days: Optional[List[str]] = None
    # Comp admission codes already issued to this guest, shown on the
    # page after confirming (same codes that were emailed).
    ticket_codes: Optional[List[str]] = None
    # Latest more-tickets request, if any: 'pending' | 'approved' | 'denied'.
    ticket_request_status: Optional[str] = None

    # Present whenever this guest holds one or more promo codes —
    # independent of is_allotment_holder, since a referrer might not be a
    # ticket-allotment holder at all, just someone with a referral code.
    referral_codes: Optional[List["ReferralCodeInfo"]] = None


class EligibleTier(BaseModel):
    redemption_tier_id: str
    points_required: int
    label: Optional[str] = None
    cash_value: Optional[float] = None
    ticket_value: Optional[int] = None
    affordable: bool


class ReferralCodeInfo(BaseModel):
    promo_code_id: str
    code: str
    reward_type: str
    points_available: Optional[int] = None  # only meaningful for a points-type code
    eligible_tiers: List[EligibleTier] = []
    redemption_history: List["RedemptionHistoryItem"] = []


class RedemptionHistoryItem(BaseModel):
    choice: str
    points_spent: int
    cash_value: Optional[float] = None
    ticket_value: Optional[int] = None
    payout_status: Optional[str] = None
    redeemed_at: str


class DistributedRecipient(BaseModel):
    name: str
    email: str
    visit_date: Optional[str] = None
    party_size: int
    allocation_status: str


class RSVPRespondRequest(BaseModel):
    attending: bool
    # 'select'-mode guests choose their own day; ignored for other modes.
    visit_date: Optional[str] = None


class RSVPTicketRequestCreate(BaseModel):
    quantity: int = Field(ge=1, le=10)
    note: Optional[str] = None


class RSVPDistributeRecipient(BaseModel):
    name: str
    email: EmailStr
    visit_date: str  # required — capacity is tracked per specific day
    party_size: int = Field(default=1, ge=1)


class RSVPDistributeRequest(BaseModel):
    recipients: List[RSVPDistributeRecipient]


class RSVPRedeemRequest(BaseModel):
    promo_code_id: str
    redemption_tier_id: str
    choice: Literal["cash", "ticket"]


RSVPInfoResponse.model_rebuild()