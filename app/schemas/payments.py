# eventnxt-backend: app/schemas/payments.py
from pydantic import BaseModel


class PaymentAccountStatus(BaseModel):
    """
    Everything the Payments card needs, nothing more. `connected` means a
    Stripe account EXISTS for the org (they clicked Connect at least
    once) — the three booleans say how far Stripe has verified it.
    """

    connected: bool
    details_submitted: bool
    charges_enabled: bool
    payouts_enabled: bool


class PaymentLinkResponse(BaseModel):
    """A one-time Stripe URL (onboarding Account Link or Express login link)."""

    url: str


class EarningsResponse(BaseModel):
    """
    THIS EVENT's native-ticketing money, summed from the order snapshots
    (the same frozen numbers the ledger will one day reconcile against).
    Externally-sold (CSV-imported) revenue is deliberately absent — that
    money never touched EventNXT.
    """

    currency: str
    gross_sold_cents: int  # charged on currently-paid orders
    platform_fees_cents: int  # kept fees (refunded orders' fees are returned)
    organizer_net_cents: int  # gross minus fees, paid orders only
    refunded_cents: int  # charged-then-returned
    paid_orders: int
    refunded_orders: int
    # Refund-cost reserve (0047): held = still-paid orders' unreleased
    # reserves (custody: platform, destiny: organizer); released = paid
    # out post-event; used = reserves of refunded orders that covered
    # those refunds' processing costs and will never release.
    reserve_held_cents: int = 0
    reserve_released_cents: int = 0
    reserve_used_cents: int = 0
    # True when the Release button should render: event over, something
    # held, payout account enabled.
    reserve_releasable: bool = False


class ReserveReleaseResponse(BaseModel):
    released_cents: int
    orders_count: int


class PayoutItem(BaseModel):
    amount_cents: int
    currency: str
    status: str  # paid / pending / in_transit / canceled / failed
    arrival_date: str  # ISO date


class PayoutsResponse(BaseModel):
    """
    ORG-scoped live numbers straight from Stripe: what's waiting out the
    7-day delay (pending), what's cleared for the next payout run
    (available), and the recent payout runs themselves. connected=False
    means no enabled account — everything else empty; never an error.
    """

    connected: bool
    balance_available_cents: int = 0
    balance_pending_cents: int = 0
    currency: str = "usd"
    payouts: list[PayoutItem] = []