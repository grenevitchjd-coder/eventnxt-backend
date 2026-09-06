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