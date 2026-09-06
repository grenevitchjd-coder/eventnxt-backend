# eventnxt-backend: test/test_destination_charges.py  (verification harness)
#
# Stripe Connect slice 2 — destination charges, the gate, and refund
# reversal:
#   1. FALLBACK: org has no payout account, enforcement OFF — a paid
#      checkout still works exactly as before Connect (no destination,
#      no fee param, snapshot NULL).
#   2. GATE: enforcement ON + no account — paid checkout 409s BEFORE
#      anything persists (no order row, no session call, no held
#      inventory), with an organizer-blaming (not buyer-blaming) message.
#   3. GATE EXEMPTION: a $0 order under enforcement still succeeds —
#      no money, nothing to route.
#   4. DESTINATION: org connected + charges_enabled — the session gets
#      transfer_data.destination + application_fee_amount equal to the
#      order's snapshotted platform fee, and the order snapshots the
#      acct id.
#   5. FEE CAP (real gateway math): a sub-dollar order where the fixed
#      75¢ fee exceeds the total — the Stripe param is capped at the
#      amount due; the ORDER's fee snapshot keeps the uncapped policy
#      number.
#   6. REFUNDS: refunding the destination order calls Stripe with
#      reverse_transfer + refund_application_fee; refunding the
#      platform-fallback order does NOT (no transfer to reverse).
#
# Run: DATABASE_URL="postgresql://test@/eventnxt_test?host=/tmp&port=5433" python3 test/test_destination_charges.py
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uuid
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql://test@/eventnxt_test?host=/tmp&port=5433")

from fastapi.testclient import TestClient

import app.services.email as email_mod
from app.config import settings as cfg
from app.database import SessionLocal
from app.main import app
from app.models.order import Order, OrderStatus
from app.models.payment_account import PaymentAccount
from app.services.deps import get_current_user
from app.services.event_access import require_event_access
import app.routers.checkout as checkout_router
import app.routers.orders_admin as orders_admin_router
import app.services.stripe_gateway as gateway
import stripe as stripe_sdk

email_mod.send_email = lambda **kw: None

EV, ORG = str(uuid.uuid4()), str(uuid.uuid4())
ACCT = "acct_TESTDEST1"
D1 = "2026-12-24"
failures = []


def check(name, cond, extra=""):
    print(("  ok " if cond else "  ✗ ") + name + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


class U:
    user_id = "u"; organization_id = ORG; name = "T"; email = "t@x.com"; role = "owner"; raw_token = "tok"
    event_data = {"organization_id": ORG, "name": "DestCharges", "start_date": D1, "end_date": D1}


app.dependency_overrides[get_current_user] = lambda: U()
app.dependency_overrides[require_event_access] = lambda event_id: U()
c = TestClient(app)
H = {"Authorization": "Bearer tok"}

TT = {"description": None, "max_per_order": 20, "admits": 1, "sales_start": None, "sales_end": None,
      "is_active": True, "sort_order": 0, "valid_date": None}

# ---- fake session creator: capture what checkout passes ----
captured = []


def fake_create_checkout_session(order, line_items_data, success_url, cancel_url, discount_cents=0,
                                 discount_label=None, destination_account_id=None, application_fee_cents=0):
    captured.append({
        "destination": destination_account_id,
        "fee": application_fee_cents,
        "order_fee_snapshot": order.platform_fee_cents,
        "order_reserve_snapshot": order.reserve_cents,
    })
    # uuid-suffixed: stripe_checkout_session_id is UNIQUE and this suite's
    # orders persist between runs — a fixed fake id collides on rerun.
    return SimpleNamespace(id=f"cs_test_{uuid.uuid4().hex}", url="https://checkout.stripe.com/test")


checkout_router.create_checkout_session = fake_create_checkout_session

# ---- fake refund: capture reversal flag (patch the module the router calls) ----
refunds = []
_real_refund = orders_admin_router.create_refund


def fake_create_refund(payment_intent_id, reverse_transfer=False, refund_application_fee=True):
    refunds.append({"pi": payment_intent_id, "reverse": reverse_transfer, "fee_back": refund_application_fee})
    return SimpleNamespace(id="re_test")


orders_admin_router.create_refund = fake_create_refund


def order_by_email(email):
    db = SessionLocal()
    o = db.query(Order).filter(Order.event_id == EV, Order.buyer_email == email).first()
    if o:
        db.expunge(o)
    db.close()
    return o


def main():
    # ---- seed: native event, one $20 type, one free type, published ----
    c.patch(f"/events/{EV}/settings", json={"ticket_span": "whole_event", "ticketing_mode": "native"}, headers=H)
    pool = c.post(f"/events/{EV}/seating-categories",
                  json={"name": "GA", "capacity": 100, "sales_grain": "ga", "row_label": None}, headers=H).json()
    paid_t = c.post(f"/events/{EV}/ticket-types",
                    json={**TT, "name": "GA Ticket", "price_cents": 2000, "quantity": 50,
                          "seating_category_id": pool["id"]}, headers=H).json()
    free_t = c.post(f"/events/{EV}/ticket-types",
                    json={**TT, "name": "Free Pass", "price_cents": 0, "quantity": 50,
                          "seating_category_id": pool["id"]}, headers=H).json()
    c.patch(f"/events/{EV}/profile/refund-policy", json={"refund_policy": "none"}, headers=H)
    slug = c.post(f"/events/{EV}/profile/publish", headers=H).json()["slug"]

    def buy(email, ticket_type_id, qty=1):
        return c.post(f"/public/events/{slug}/checkout",
                      json={"buyer_name": "B", "buyer_email": email,
                            "items": [{"ticket_type_id": ticket_type_id, "quantity": qty,
                                       "seat_ids": [], "zone_section_id": None, "zone_section_ids": []}]})

    print("1) fallback: no account, enforcement off")
    cfg.stripe_require_connected_account = False
    r = buy("platform@x.com", paid_t["id"])
    check("checkout 200", r.status_code == 200, r.text[:120])
    check("no destination on the session", captured[-1]["destination"] is None)
    o = order_by_email("platform@x.com")
    check("order snapshot NULL", o is not None and o.stripe_destination_account is None)

    print("2) gate: enforcement on, no account")
    cfg.stripe_require_connected_account = True
    n_calls = len(captured)
    r = buy("blocked@x.com", paid_t["id"])
    check("409", r.status_code == 409, f"{r.status_code} {r.text[:120]}")
    check("message blames payout setup, not the buyer", "payout setup" in r.json()["detail"])
    check("no session call made", len(captured) == n_calls)
    check("no order persisted", order_by_email("blocked@x.com") is None)

    print("3) gate exemption: $0 order still succeeds under enforcement")
    r = buy("freebie@x.com", free_t["id"])
    check("free checkout 200 paid-now", r.status_code == 200 and r.json()["status"] == "paid", r.text[:120])

    print("4) destination charge once connected + enabled")
    db = SessionLocal()
    db.add(PaymentAccount(organization_id=ORG, stripe_account_id=ACCT,
                          charges_enabled=True, payouts_enabled=True, details_submitted=True))
    db.commit(); db.close()
    r = buy("dest@x.com", paid_t["id"], qty=2)
    check("checkout 200", r.status_code == 200, r.text[:120])
    check("session destination = connected acct", captured[-1]["destination"] == ACCT)
    # PIN REWRITTEN for 0047: the app fee sent to Stripe is now the
    # platform fee PLUS the withheld refund-cost reserve.
    check("session fee == platform fee + reserve snapshots",
          captured[-1]["fee"] == captured[-1]["order_fee_snapshot"] + captured[-1]["order_reserve_snapshot"]
          and captured[-1]["order_reserve_snapshot"] > 0,
          str(captured[-1]))
    o_dest = order_by_email("dest@x.com")
    check("order snapshots the acct id", o_dest is not None and o_dest.stripe_destination_account == ACCT)

    print("5) fee cap in the REAL gateway (sub-dollar order)")
    stripe_params = {}
    real_session_create = stripe_sdk.checkout.Session.create
    stripe_sdk.checkout.Session.create = lambda **kw: (stripe_params.update(kw), SimpleNamespace(id="cs_x", url="u"))[1]
    fake_order = SimpleNamespace(id=uuid.uuid4(), order_token="tok", buyer_email="tiny@x.com", platform_fee_cents=77)
    gateway.create_checkout_session(
        fake_order,
        line_items_data=[{"name": "Tiny", "unit_price_cents": 50, "quantity": 1, "currency": "usd"}],
        success_url="s", cancel_url="c",
        destination_account_id=ACCT, application_fee_cents=77,
    )
    stripe_sdk.checkout.Session.create = real_session_create
    pid = stripe_params.get("payment_intent_data")
    check("fee capped at the 50¢ due (not the 77¢ policy)", pid and pid["application_fee_amount"] == 50, str(pid))
    check("destination rides the payment intent", pid and pid["transfer_data"]["destination"] == ACCT)

    print("6) refund reversal follows the snapshot")
    db = SessionLocal()
    for o, pi in ((order_by_email("dest@x.com"), "pi_dest"), (order_by_email("platform@x.com"), "pi_plat")):
        row = db.query(Order).filter(Order.id == o.id).first()
        row.status = OrderStatus.PAID
        row.stripe_payment_intent_id = pi
    db.commit(); db.close()
    o_dest = order_by_email("dest@x.com")
    r = c.post(f"/events/{EV}/orders/{o_dest.id}/refund", headers=H)
    check("destination refund 200", r.status_code == 200, r.text[:120])
    # PIN REWRITTEN for 0047: an unreleased reserve absorbs the refund's
    # processing cost, so the withheld app fee is NOT returned.
    check("reverse_transfer set, app fee kept (reserve absorbs)",
          refunds[-1] == {"pi": "pi_dest", "reverse": True, "fee_back": False}, str(refunds[-1]))
    o_plat = order_by_email("platform@x.com")
    r = c.post(f"/events/{EV}/orders/{o_plat.id}/refund", headers=H)
    check("platform refund 200", r.status_code == 200, r.text[:120])
    check("no reversal on a platform charge",
          refunds[-1]["pi"] == "pi_plat" and refunds[-1]["reverse"] is False, str(refunds[-1]))

    # ---- cleanup ----
    cfg.stripe_require_connected_account = False
    db = SessionLocal()
    db.query(PaymentAccount).filter(PaymentAccount.organization_id == ORG).delete()
    db.commit(); db.close()


main()
print()
if failures:
    print(f"FAILED: {len(failures)} — " + ", ".join(failures))
    sys.exit(1)
print("test_destination_charges: all checks passed")