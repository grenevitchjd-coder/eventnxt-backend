# eventnxt-backend: app/routers/payments.py
"""
Stripe Connect, slice 1: the organizer's payout account.

Endpoints are EVENT-scoped (matching every other router and riding
require_event_access) but the account itself is ORG-scoped — the
resolution from event to org happens here via the caller's identity, so
connecting from any one event lights up all of the org's events.

The flow: POST /connect creates the Express account on first click (the
acct id is committed BEFORE the Account Link is requested — a link
failure must never lose an account we created) and returns a one-time
Stripe-hosted onboarding URL. Account Links die in minutes and are
single-use, so every click mints a fresh one; "resume onboarding" is the
same endpoint. Status flows back exclusively through the account.updated
Connect webhook — we never compute the booleans locally.

Nothing sensitive ever transits this router: no bank details, no tax
ids, no identity data. Stripe collects all of that on its own hosted
pages.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import stripe as stripe_lib

from app.config import settings
from app.database import get_db
from datetime import datetime, timezone

from sqlalchemy import func

from app.models.order import Order, OrderStatus
from app.models.payment_account import PaymentAccount
from app.models.stripe_webhook_event import StripeWebhookEvent
from app.schemas.payments import (
    EarningsResponse,
    PaymentAccountStatus,
    PaymentLinkResponse,
    PayoutItem,
    PayoutsResponse,
)
from app.services import stripe_gateway as gateway
from app.services.deps import CurrentUser
from app.services.event_access import require_event_access

router = APIRouter(tags=["payments"])


def _org_account(db: Session, organization_id) -> PaymentAccount | None:
    return db.query(PaymentAccount).filter(PaymentAccount.organization_id == organization_id).first()


@router.get("/events/{event_id}/payments", response_model=PaymentAccountStatus)
def payment_status(
    event_id: str,
    user: CurrentUser = Depends(require_event_access),
    db: Session = Depends(get_db),
):
    account = _org_account(db, user.organization_id)
    if not account:
        return PaymentAccountStatus(
            connected=False, details_submitted=False, charges_enabled=False, payouts_enabled=False
        )
    return PaymentAccountStatus(
        connected=True,
        details_submitted=account.details_submitted,
        charges_enabled=account.charges_enabled,
        payouts_enabled=account.payouts_enabled,
    )


@router.post("/events/{event_id}/payments/connect", response_model=PaymentLinkResponse)
def connect_payments(
    event_id: str,
    user: CurrentUser = Depends(require_event_access),
    db: Session = Depends(get_db),
):
    """
    First click: create the Express account, save it, hand back the
    onboarding URL. Every later click (unfinished onboarding, expired
    link, Stripe's refresh_url bounce): reuse the saved account, mint a
    fresh link. Idempotent from the organizer's point of view — the
    button is always safe to press.
    """
    account = _org_account(db, user.organization_id)

    if not account:
        try:
            stripe_account = gateway.create_express_account(str(user.organization_id))
        except stripe_lib.error.StripeError:
            raise HTTPException(status_code=502, detail="Stripe couldn't create the account. Try again in a moment.")
        account = PaymentAccount(
            organization_id=user.organization_id,
            stripe_account_id=stripe_account["id"],
        )
        db.add(account)
        try:
            # Committed BEFORE the Account Link call: if the link request
            # fails we must still remember the account we just created —
            # otherwise the next click creates a duplicate Stripe account.
            db.commit()
        except IntegrityError:
            # Two tabs raced the first click; the other one won. Use theirs.
            db.rollback()
            account = _org_account(db, user.organization_id)
            if not account:
                raise HTTPException(status_code=502, detail="Could not save the payout account. Try again.")

    # The return/refresh params only route the browser back to Event
    # settings — the payments flag tells the dashboard which tab to open.
    return_url = f"{settings.eventnxt_frontend_url}/?payments=return"
    refresh_url = f"{settings.eventnxt_frontend_url}/?payments=refresh"
    try:
        link = gateway.create_account_link(account.stripe_account_id, return_url, refresh_url)
    except stripe_lib.error.StripeError:
        raise HTTPException(status_code=502, detail="Stripe couldn't start onboarding. Try again in a moment.")
    return PaymentLinkResponse(url=link["url"])


@router.post("/events/{event_id}/payments/manage-link", response_model=PaymentLinkResponse)
def manage_payments_link(
    event_id: str,
    user: CurrentUser = Depends(require_event_access),
    db: Session = Depends(get_db),
):
    """
    One-time login link into the organizer's Stripe Express dashboard
    (balance, payout history, changing the bank account — none of which
    EventNXT ever touches). Stripe only honors these once onboarding is
    complete, so gate on details_submitted with a pointer back to the
    Connect button.
    """
    account = _org_account(db, user.organization_id)
    if not account or not account.details_submitted:
        raise HTTPException(status_code=400, detail="Finish connecting payouts first.")
    try:
        link = gateway.create_login_link(account.stripe_account_id)
    except stripe_lib.error.StripeError:
        raise HTTPException(status_code=502, detail="Stripe couldn't open the payouts dashboard. Try again in a moment.")
    return PaymentLinkResponse(url=link["url"])


@router.get("/events/{event_id}/payments/earnings", response_model=EarningsResponse)
def event_earnings(
    event_id: str,
    user: CurrentUser = Depends(require_event_access),
    db: Session = Depends(get_db),
):
    """
    This event's native money, from the orders table's frozen snapshots —
    works with or without a connected account (platform-fallback-era
    sales count the same). Fee policy is visible in the shape: refunded
    orders keep their gross in refunded_cents but contribute NOTHING to
    platform_fees_cents or organizer_net_cents (no platform fee on
    refunded tickets — the fee snapshot on those rows records what WAS
    charged, not what was kept).
    """
    def sums(status):
        row = (
            db.query(
                func.coalesce(func.sum(Order.subtotal_cents - Order.discount_cents), 0),
                func.coalesce(func.sum(Order.platform_fee_cents), 0),
                func.coalesce(func.sum(Order.organizer_net_cents), 0),
                func.count(Order.id),
            )
            .filter(Order.event_id == event_id, Order.status == status)
            .one()
        )
        return {"gross": int(row[0]), "fees": int(row[1]), "net": int(row[2]), "count": int(row[3])}

    paid = sums(OrderStatus.PAID)
    refunded = sums(OrderStatus.REFUNDED)
    currency_row = db.query(Order.currency).filter(Order.event_id == event_id).first()
    return EarningsResponse(
        currency=(currency_row[0] if currency_row else "usd"),
        gross_sold_cents=paid["gross"],
        platform_fees_cents=paid["fees"],
        organizer_net_cents=paid["net"],
        refunded_cents=refunded["gross"],
        paid_orders=paid["count"],
        refunded_orders=refunded["count"],
    )


@router.get("/events/{event_id}/payments/payouts", response_model=PayoutsResponse)
def payout_summary(
    event_id: str,
    user: CurrentUser = Depends(require_event_access),
    db: Session = Depends(get_db),
):
    """
    The org's live money at Stripe: pending (waiting out the 7-day
    delay), available (cleared for the next payout run), and recent
    payouts. No enabled account is a NORMAL state, not an error —
    connected=False and empty numbers, so the Orders page renders the
    same panel code everywhere.
    """
    account = _org_account(db, user.organization_id)
    if not account or not account.charges_enabled:
        return PayoutsResponse(connected=False)

    def cents(entries):
        return sum(int(e.get("amount", 0)) for e in entries if e.get("currency", "usd") == "usd")

    try:
        balance = gateway.retrieve_balance(account.stripe_account_id)
        payouts = gateway.list_payouts(account.stripe_account_id, limit=10)
    except stripe_lib.error.StripeError:
        raise HTTPException(status_code=502, detail="Stripe couldn't report the balance. Try again in a moment.")

    return PayoutsResponse(
        connected=True,
        balance_available_cents=cents(balance.get("available", [])),
        balance_pending_cents=cents(balance.get("pending", [])),
        currency="usd",
        payouts=[
            PayoutItem(
                amount_cents=int(p.get("amount", 0)),
                currency=p.get("currency", "usd"),
                status=p.get("status", ""),
                arrival_date=datetime.fromtimestamp(int(p.get("arrival_date", 0)), tz=timezone.utc).date().isoformat(),
            )
            for p in payouts.get("data", [])
        ],
    )


# ---------- Stripe Connect webhook ----------


@router.post("/webhooks/stripe-connect")
async def stripe_connect_webhook(request: Request, db: Session = Depends(get_db)):
    """
    The ONLY writer of the three status booleans. Same shape as the
    platform webhook in checkout.py: verify signature (fail closed on a
    missing secret), dedupe through stripe_webhook_events' unique
    constraint, then mirror the account's flags.
    """
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")

    try:
        event = gateway.construct_connect_webhook_event(payload, signature)
    except gateway.WebhookNotConfigured:
        raise HTTPException(status_code=503, detail="Webhook not configured.")
    except (ValueError, stripe_lib.error.SignatureVerificationError):
        raise HTTPException(status_code=400, detail="Invalid signature.")

    db.add(StripeWebhookEvent(stripe_event_id=event["id"], event_type=event["type"]))
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return {"status": "already_processed"}

    if event["type"] == "account.updated":
        acct = event["data"]["object"]
        account = (
            db.query(PaymentAccount)
            .filter(PaymentAccount.stripe_account_id == acct["id"])
            .with_for_update()
            .first()
        )
        if account:
            account.charges_enabled = bool(acct.get("charges_enabled"))
            account.payouts_enabled = bool(acct.get("payouts_enabled"))
            account.details_submitted = bool(acct.get("details_submitted"))
            db.commit()
            return {"status": "updated"}
        db.commit()  # record the webhook event even for an account we don't know
        return {"status": "no_action"}

    db.commit()  # unhandled event types: record and acknowledge
    return {"status": "ignored"}