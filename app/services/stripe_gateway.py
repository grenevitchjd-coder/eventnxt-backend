"""
eventnxt-backend: app/services/stripe_gateway.py

The only file that talks to Stripe. Thin on purpose: create a Checkout
Session, verify a webhook signature — nothing else knows Stripe exists.

Fail-closed rule: with no webhook secret configured, webhook verification
refuses EVERYTHING. An unauthenticated endpoint that "temporarily" skips
signature checks is how fake payments get injected; better a dead webhook
than an open one.
"""

from datetime import datetime, timedelta, timezone

import stripe

from app.config import settings
from app.models.order import Order


class WebhookNotConfigured(Exception):
    pass


def _client() -> None:
    stripe.api_key = settings.stripe_secret_key


def create_checkout_session(order: Order, line_items_data: list[dict], success_url: str, cancel_url: str, discount_cents: int = 0, discount_label: str | None = None, destination_account_id: str | None = None, application_fee_cents: int = 0):
    """
    line_items_data: [{'name': ..., 'unit_price_cents': ..., 'quantity': ..., 'currency': ...}]
    The session expires when our pending hold does — the two deadlines are
    the same 30 minutes on purpose, so Stripe never accepts a payment for
    a hold we've already released.

    destination_account_id (Connect, slice 2): when set, the payment is a
    DESTINATION CHARGE — the buyer pays the platform, Stripe atomically
    transfers the organizer's share to their connected account, and
    application_fee_cents stays with the platform. Stripe's processing
    fee comes out of the platform's side, which is exactly the fee model
    (organizer pays the flat platform fee, platform keeps the margin over
    Stripe's cost). When None, the charge settles entirely into the
    platform account, exactly as before Connect existed.
    """
    _client()
    # A promo discount becomes an ad-hoc single-use Stripe coupon, so the
    # buyer sees face-value line items plus an explicit discount line
    # ("ROW10  -$4.00") — honest receipts beat silently-adjusted prices.
    discounts = None
    if discount_cents > 0:
        coupon = stripe.Coupon.create(
            amount_off=discount_cents,
            currency=line_items_data[0]["currency"],
            duration="once",
            name=(discount_label or "Discount")[:40],
        )
        discounts = [{"coupon": coupon.id}]
    payment_intent_data = None
    if destination_account_id:
        # Stripe rejects an application fee above the amount actually
        # charged — only reachable on a sub-dollar order where the fixed
        # 75¢ exceeds the total. The ORDER's fee snapshot is untouched
        # (it records the policy); the cap only bounds what Stripe moves.
        amount_due = sum(li["unit_price_cents"] * li["quantity"] for li in line_items_data) - discount_cents
        payment_intent_data = {
            "transfer_data": {"destination": destination_account_id},
            "application_fee_amount": max(0, min(application_fee_cents, amount_due)),
        }
    session = stripe.checkout.Session.create(
        mode="payment",
        payment_intent_data=payment_intent_data,
        line_items=[
            {
                "price_data": {
                    "currency": li["currency"],
                    "unit_amount": li["unit_price_cents"],
                    "product_data": {"name": li["name"]},
                },
                "quantity": li["quantity"],
            }
            for li in line_items_data
        ],
        customer_email=order.buyer_email,
        metadata={"order_id": str(order.id), "order_token": order.order_token},
        success_url=success_url,
        cancel_url=cancel_url,
        expires_at=int((datetime.now(timezone.utc) + timedelta(minutes=30)).timestamp()),
        discounts=discounts,
    )
    return session


def construct_webhook_event(payload: bytes, signature_header: str):
    """Verifies Stripe's signature and returns the event. Raises on any mismatch."""
    if not settings.stripe_webhook_secret:
        raise WebhookNotConfigured("STRIPE_WEBHOOK_SECRET is not set — refusing all webhooks (fail closed).")
    return stripe.Webhook.construct_event(payload, signature_header, settings.stripe_webhook_secret)


def create_refund(payment_intent_id: str, reverse_transfer: bool = False, refund_application_fee: bool = True):
    """Full refund of the payment. Stripe keeps its processing fee from
    the original charge — the two flags decide whose ledger shows it.

    reverse_transfer=True (destination charges only): claw the
    organizer's transferred share back from their connected balance.
    Never set it for a platform-account charge (no transfer to reverse;
    Stripe would error).

    refund_application_fee (only meaningful with a reversal):
      True  — the withheld app-fee side returns too; the platform eats
              the processing cost (pre-reserve behavior; post-release
              and legacy orders).
      False — the platform KEEPS the withheld app fee. With the 0047
              reserve inside that fee, the reserve covers the processing
              cost and the organizer bears it by that order's reserve
              never releasing."""
    _client()
    kwargs: dict = {"payment_intent": payment_intent_id}
    if reverse_transfer:
        kwargs["reverse_transfer"] = True
        kwargs["refund_application_fee"] = refund_application_fee
    return stripe.Refund.create(**kwargs)

# ---------- Stripe Connect (organizer payout accounts) ----------
#
# Express accounts, destination charges. EventNXT never collects payment
# details itself: create the account, hand the organizer to Stripe-hosted
# onboarding via an Account Link, and mirror status back through the
# account.updated webhook. The 7-day payout delay is set here at account
# creation — it buffers refunds/disputes against balances that have
# already paid out (the platform eats failed debits under destination
# charges, so the delay is our exposure control).

PAYOUT_DELAY_DAYS = 7


def create_express_account(organization_id: str):
    _client()
    return stripe.Account.create(
        type="express",
        capabilities={
            "card_payments": {"requested": True},
            "transfers": {"requested": True},
        },
        settings={"payouts": {"schedule": {"delay_days": PAYOUT_DELAY_DAYS, "interval": "daily"}}},
        metadata={"organization_id": str(organization_id)},
    )


def create_account_link(stripe_account_id: str, return_url: str, refresh_url: str):
    """
    A one-time, minutes-lived URL into Stripe-hosted Express onboarding.
    Expired/used links are NEVER an error state for us — mint a fresh one
    on every click; that's what refresh_url exists for on Stripe's side.
    """
    _client()
    return stripe.AccountLink.create(
        account=stripe_account_id,
        return_url=return_url,
        refresh_url=refresh_url,
        type="account_onboarding",
    )


def create_login_link(stripe_account_id: str):
    """
    A one-time URL into the organizer's Stripe Express dashboard (balance,
    payout history, bank-account changes). Only valid once onboarding is
    complete — callers gate on details_submitted.
    """
    _client()
    return stripe.Account.create_login_link(stripe_account_id)


def construct_connect_webhook_event(payload: bytes, signature_header: str):
    """Same fail-closed rule as the platform webhook, separate secret."""
    if not settings.stripe_connect_webhook_secret:
        raise WebhookNotConfigured(
            "STRIPE_CONNECT_WEBHOOK_SECRET is not set — refusing all Connect webhooks (fail closed)."
        )
    return stripe.Webhook.construct_event(payload, signature_header, settings.stripe_connect_webhook_secret)


def retrieve_balance(stripe_account_id: str):
    """The connected account's live balance (available vs pending)."""
    _client()
    return stripe.Balance.retrieve(stripe_account=stripe_account_id)


def list_payouts(stripe_account_id: str, limit: int = 10):
    """The connected account's most recent bank payouts, newest first."""
    _client()
    return stripe.Payout.list(stripe_account=stripe_account_id, limit=limit)