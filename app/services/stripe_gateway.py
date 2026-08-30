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


def create_checkout_session(order: Order, line_items_data: list[dict], success_url: str, cancel_url: str):
    """
    line_items_data: [{'name': ..., 'unit_price_cents': ..., 'quantity': ..., 'currency': ...}]
    The session expires when our pending hold does — the two deadlines are
    the same 30 minutes on purpose, so Stripe never accepts a payment for
    a hold we've already released.
    """
    _client()
    session = stripe.checkout.Session.create(
        mode="payment",
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
    )
    return session


def construct_webhook_event(payload: bytes, signature_header: str):
    """Verifies Stripe's signature and returns the event. Raises on any mismatch."""
    if not settings.stripe_webhook_secret:
        raise WebhookNotConfigured("STRIPE_WEBHOOK_SECRET is not set — refusing all webhooks (fail closed).")
    return stripe.Webhook.construct_event(payload, signature_header, settings.stripe_webhook_secret)