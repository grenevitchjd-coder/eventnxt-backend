# eventnxt-backend: test/test_reserve.py  (verification harness)
#
# Refund-cost reserve (migration 0047), slice A:
#   1. A destination order snapshots reserve = round(charged × 2.9%) + 30¢
#      on the (subtotal − discount) basis, and the session's app fee is
#      platform fee + reserve.
#   2. No reserve where there's nothing to withhold: platform-fallback
#      orders and $0 orders carry reserve 0.
#   3. Refund routing by reserve state:
#      - unreleased reserve  -> app fee KEPT (reserve absorbs the cost)
#      - released reserve    -> app fee returned (platform eats it)
#      - zero-reserve legacy -> app fee returned (pre-0047 behavior)
#   4. THE CANCELLATION LEDGER — the formula slice B's release endpoint
#      will pay out: SUM(reserve) OVER STILL-PAID ORDERS. Refund one of
#      three and the sum is exactly the two kept reserves; refund all
#      (event cancelled) and the sum is zero — the organizer bears every
#      refund's processing cost by non-payment, never by a debit.
#
# Run: DATABASE_URL="postgresql://test@/eventnxt_test?host=/tmp&port=5433" python3 test/test_reserve.py
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql://test@/eventnxt_test?host=/tmp&port=5433")

from fastapi.testclient import TestClient
from sqlalchemy import func

import app.services.email as email_mod
from app.database import SessionLocal
from app.main import app
from app.models.order import Order, OrderStatus
from app.models.payment_account import PaymentAccount
from app.services.deps import get_current_user
from app.services.event_access import require_event_access
import app.routers.checkout as checkout_router
import app.routers.orders_admin as orders_admin_router

email_mod.send_email = lambda **kw: None

EV, ORG = str(uuid.uuid4()), str(uuid.uuid4())
# uuid-suffixed: stripe_account_id is UNIQUE and a crashed run leaves
# its row behind — a fixed fake id would collide on rerun.
ACCT = f"acct_TESTRESERVE_{uuid.uuid4().hex[:10]}"
failures = []


def check(name, cond, extra=""):
    print(("  ok " if cond else "  ✗ ") + name + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


class U:
    user_id = "u"; organization_id = ORG; name = "T"; email = "t@x.com"; role = "owner"; raw_token = "tok"
    event_data = {"organization_id": ORG, "name": "Reserve", "start_date": "2026-12-24", "end_date": "2026-12-24"}


app.dependency_overrides[get_current_user] = lambda: U()
app.dependency_overrides[require_event_access] = lambda event_id: U()
c = TestClient(app)
H = {"Authorization": "Bearer tok"}

TT = {"description": None, "max_per_order": 20, "admits": 1, "sales_start": None, "sales_end": None,
      "is_active": True, "sort_order": 0, "valid_date": None}

captured = []


def fake_session(order, line_items_data, success_url, cancel_url, discount_cents=0, discount_label=None,
                 destination_account_id=None, application_fee_cents=0):
    captured.append({"fee_param": application_fee_cents, "fee": order.platform_fee_cents,
                     "reserve": order.reserve_cents})
    return SimpleNamespace(id=f"cs_test_{uuid.uuid4().hex}", url="https://checkout.stripe.com/test")


checkout_router.create_checkout_session = fake_session

refunds = []
orders_admin_router.create_refund = lambda pi, reverse_transfer=False, refund_application_fee=True: (
    refunds.append({"reverse": reverse_transfer, "fee_back": refund_application_fee}) or SimpleNamespace(id="re")
)


def order_by_email(email):
    db = SessionLocal()
    o = db.query(Order).filter(Order.event_id == EV, Order.buyer_email == email).first()
    if o:
        db.expunge(o)
    db.close()
    return o


def mark_paid(email, pi):
    db = SessionLocal()
    row = db.query(Order).filter(Order.event_id == EV, Order.buyer_email == email).first()
    row.status = OrderStatus.PAID
    row.stripe_payment_intent_id = pi
    oid = row.id  # read BEFORE commit expires + close detaches the row
    db.commit(); db.close()
    return oid


def paid_reserve_sum():
    db = SessionLocal()
    total = (
        db.query(func.coalesce(func.sum(Order.reserve_cents), 0))
        .filter(Order.event_id == EV, Order.status == OrderStatus.PAID)
        .scalar()
    )
    db.close()
    return int(total)


def main():
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

    def buy(email, tt=None, qty=1):
        return c.post(f"/public/events/{slug}/checkout",
                      json={"buyer_name": "B", "buyer_email": email,
                            "items": [{"ticket_type_id": (tt or paid_t)["id"], "quantity": qty,
                                       "seat_ids": [], "zone_section_id": None, "zone_section_ids": []}]})

    print("1) platform-fallback order carries no reserve")
    r = buy("plat@x.com")
    check("checkout 200", r.status_code == 200, r.text[:120])
    check("reserve 0, fee param = fee alone", captured[-1]["reserve"] == 0 and captured[-1]["fee_param"] == captured[-1]["fee"], str(captured[-1]))

    print("2) destination order snapshots the reserve")
    db = SessionLocal()
    db.add(PaymentAccount(organization_id=ORG, stripe_account_id=ACCT,
                          charges_enabled=True, payouts_enabled=True, details_submitted=True))
    db.commit(); db.close()
    for i in range(3):
        r = buy(f"dest{i}@x.com", qty=2)  # charged 4000 -> reserve round(4000*.029)+30 = 146
        check(f"checkout {i} 200", r.status_code == 200, r.text[:120])
    check("reserve = round(4000×2.9%)+30 = 146", captured[-1]["reserve"] == 146, str(captured[-1]))
    check("fee param = fee + reserve", captured[-1]["fee_param"] == captured[-1]["fee"] + 146, str(captured[-1]))

    print("3) $0 order carries no reserve")
    r = buy("free@x.com", tt=free_t)
    check("free 200 paid-now", r.status_code == 200 and r.json()["status"] == "paid", r.text[:120])
    o = order_by_email("free@x.com")
    check("reserve 0 on the $0 order", o.reserve_cents == 0)

    print("4) refund routing by reserve state")
    ids = [mark_paid(f"dest{i}@x.com", f"pi_{i}") for i in range(3)]
    mark_paid("plat@x.com", "pi_plat")
    r = c.post(f"/events/{EV}/orders/{ids[0]}/refund", headers=H)
    check("unreleased reserve: app fee KEPT", r.status_code == 200 and refunds[-1] == {"reverse": True, "fee_back": False}, str(refunds[-1]))
    # released reserve -> pre-reserve behavior (stamp release directly; the endpoint is slice B)
    db = SessionLocal()
    row = db.query(Order).filter(Order.id == ids[1]).first()
    row.reserve_released_at = datetime.now(timezone.utc)
    db.commit(); db.close()
    r = c.post(f"/events/{EV}/orders/{ids[1]}/refund", headers=H)
    check("released reserve: app fee returned", r.status_code == 200 and refunds[-1] == {"reverse": True, "fee_back": True}, str(refunds[-1]))
    # zero-reserve destination legacy order (pre-0047 shape)
    db = SessionLocal()
    legacy = Order(event_id=EV, organization_id=ORG, status=OrderStatus.PAID, buyer_name="L",
                   buyer_email="legacy@x.com", currency="usd", subtotal_cents=1000, discount_cents=0,
                   platform_fee_cents=105, organizer_net_cents=895, reserve_cents=0,
                   stripe_destination_account=ACCT, stripe_payment_intent_id="pi_legacy",
                   order_token="T" + uuid.uuid4().hex[:10].upper())
    db.add(legacy); db.commit(); legacy_id = legacy.id; db.close()
    r = c.post(f"/events/{EV}/orders/{legacy_id}/refund", headers=H)
    check("legacy zero-reserve: app fee returned", r.status_code == 200 and refunds[-1] == {"reverse": True, "fee_back": True}, str(refunds[-1]))

    print("5) the cancellation ledger: release = sum over still-paid")
    # dest2 is the only reserved order still paid (dest0 refunded, dest1 refunded-after-release)
    check("one kept order -> one reserve in the sum", paid_reserve_sum() == 146, paid_reserve_sum())
    r = c.post(f"/events/{EV}/orders/{ids[2]}/refund", headers=H)
    check("full cancellation -> release sum is ZERO", r.status_code == 200 and paid_reserve_sum() == 0, paid_reserve_sum())

    # ---- cleanup ----
    db = SessionLocal()
    db.query(PaymentAccount).filter(PaymentAccount.organization_id == ORG).delete()
    db.commit(); db.close()


main()
print()
if failures:
    print(f"FAILED: {len(failures)} — " + ", ".join(failures))
    sys.exit(1)
print("test_reserve: all checks passed")