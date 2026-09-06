# eventnxt-backend: app/schemas/rsvp.py
from typing import Dict, List, Literal, Optional

from datetime import datetime
from pydantic import BaseModel, EmailStr, Field


class DayGrantItem(BaseModel):
    date: str
    quantity: int


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
    outreach_terms_accepted_at: Optional[datetime] = None  # 0051: referrer accepted the Outreach Policy
    # 0052: the payout-terms wall. True = this guest holds referral codes
    # but hasn't accepted — referral_codes are WITHHELD from this payload
    # until POST /accept-payout-terms with a full legal name.
    payout_terms_required: bool = False
    payout_terms_accepted_at: Optional[datetime] = None
    guest_type_name: Optional[str] = None  # None for referrer-only guests
    allocation_status: str
    visit_date: Optional[str] = None
    party_size: int
    # Invite/select guests: their per-day grant (invite: what they were
    # given; select: the offered days and per-day caps).
    day_grants: Optional[List[DayGrantItem]] = None

    is_allotment_holder: bool
    day_allotments: Optional[List[DayAllotment]] = None
    distributed_recipients: Optional[List["DistributedRecipient"]] = None

    # The guest's actual experience ('invite' | 'distribute' | 'select'),
    # so the page renders the right interaction without re-deriving it.
    effective_mode: str = "invite"
    # Choose-within-caps: total the guest may place when it's under the
    # sum of their day grants (grants become ceilings). Null otherwise.
    spend_total: Optional[int] = None
    choose_within_caps: bool = False
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


class ReferralContactInfo(BaseModel):
    """One invited person's outreach status, shown on the portal."""

    name: str
    email: str
    sent_at: Optional[datetime] = None
    clicked: bool = False
    tickets_bought: int = 0
    amount_bought: float = 0


class ReferContactItem(BaseModel):
    name: str
    email: EmailStr


class PayoutTermsAcceptRequest(BaseModel):
    # The e-signature: a typed full legal name, recorded with the stamp.
    legal_name: str = Field(min_length=3, max_length=150)


class RSVPReferRequest(BaseModel):
    promo_code_id: str
    contacts: List[ReferContactItem]
    # Optional personalization (2026-09-05): the referrer's own words.
    # The tracked link, discount line, and on-behalf-of footer are ALWAYS
    # appended server-side — the referrer customizes the message, never
    # the mechanism. Caps are anti-abuse: this pipes user-written text
    # through the platform's own outbound email.
    subject: Optional[str] = Field(default=None, max_length=150)
    message: Optional[str] = Field(default=None, max_length=2000)
    # 0051: required TRUE on the referrer's first send (until their
    # acceptance is stamped) — the same enforce-then-stamp shape as the
    # purchasing agreement's terms_accepted.
    outreach_terms_accepted: bool = False


class DealPointsRate(BaseModel):
    ticket_type: str
    points: int


class DealBonusTier(BaseModel):
    tickets_required: int
    bonus_value: float


class ReferralCodeInfo(BaseModel):
    promo_code_id: str
    code: str
    reward_type: str
    # ---- The payout agreement, spelled out (2026-09-05 request) ----
    # reward_value in the reward_type's unit ($ per ticket, % of sale,
    # free tickets per ticket); points codes use points_rates instead.
    # bonus_tiers is what ACTUALLY applies — this code's override when
    # set, else the event default (effective_bonus_tiers).
    reward_value: Optional[float] = None
    points_rates: List[DealPointsRate] = []
    bonus_tiers: List[DealBonusTier] = []
    points_available: Optional[int] = None  # only meaningful for a points-type code
    eligible_tiers: List[EligibleTier] = []
    redemption_history: List["RedemptionHistoryItem"] = []
    # ---- Progress dashboard (Promote-redesign slice D) ----
    # Aggregated by the SAME function that feeds the organizer's
    # /promo-stats, so referrer and organizer always see one truth.
    tickets_sold: int = 0
    amount_sold: float = 0
    rows_missing_amount: int = 0
    link_clicks: int = 0
    # Accrued reward in the code's own unit ($ / tickets / points) —
    # the "estimated payout" for non-points deals; points deals read
    # points_available + eligible_tiers instead.
    total_reward: Optional[float] = None
    # So the portal can tell followers what the code is worth to THEM.
    discount_type: Optional[str] = None
    discount_value: Optional[float] = None
    # Prefill for the refer tab's message box (organizer's suggested text).
    referral_message_draft: Optional[str] = None
    contacts: List[ReferralContactInfo] = []


class RedemptionHistoryItem(BaseModel):
    choice: str
    points_spent: int
    cash_value: Optional[float] = None
    ticket_value: Optional[int] = None
    payout_status: Optional[str] = None
    redeemed_at: str


class DistributedRecipient(BaseModel):
    id: Optional[str] = None  # for the portal's remove action
    name: str
    email: str
    visit_date: Optional[str] = None
    party_size: int
    allocation_status: str
    rsvp_confirmed: Optional[str] = None  # null=no answer, "yes", "no"
    rsvp_link: Optional[str] = None  # for manual forwarding


class RSVPRespondRequest(BaseModel):
    attending: bool
    # 'select'-mode guests choose their own day; ignored for other modes.
    visit_date: Optional[str] = None
    # Per-day acceptance grid (invite: reduce-only against the grant;
    # select: distribute up to party_size across the offered days).
    day_quantities: Optional[Dict[str, int]] = None


class RSVPTicketRequestCreate(BaseModel):
    quantity: int = Field(ge=1, le=10)
    note: Optional[str] = None
    date: Optional[str] = None  # day-granted guests ask per day


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