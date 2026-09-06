# eventnxt-backend: test/test_earnings.py  (verification harness)
#
# Stripe Connect slice 3 — the Orders-page Earnings panel's numbers:
#   1. /payments/earnings sums ONLY money states: PAID rows feed gross /
#      fees / net; REFUNDED rows feed refunded_cents and contribute
#      NOTHING to fees or net (no platform fee on refunded tickets);
#      PENDING and EXPIRED rows count nowhere.
#   2. A $0 comp order (fee 0, net 0) inflates counts, not money.
#   3. An event with no orders returns clean zeros, not an error.
#   4. /payments/payouts with no enabled account: connected=false and
#      zeros — a normal state, never an error.
#   5. Connected: Stripe's balance buckets sum (usd entries only) and
#      payouts arrive as ISO dates; non-usd balance entries are ignored
#      rather than mis-added into a usd total.
#
# Run: DATABASE_URL="postgresql://test@/eventnxt_test?host=/tmp&port=5433" python3 test/test_earnings.py
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uuid

os.environ.setdefault("DATABASE_URL", "postgresql://test@/eventnxt_test?host=/tmp&port=5433")

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models.order import Order, OrderStatus
from app.models.payment_account import PaymentAccount
from app.services import stripe_gateway as gateway
from app.services.deps import get_current_user
from app.services.event_access import require_event_access

EV, EV_EMPTY, ORG = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
ACCT = "acct_TESTEARN1"
failures = []


def check(name, cond, extra=""):
    print(("  ok " if cond else "  ✗ ") + name + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


class U:
    user_id = "u"; organization_id = ORG; name = "T"; email = "t@x.com"; role = "owner"; raw_token = "tok"
    event_data = {"organization_id": ORG, "name": "Earnings", "start_date": "2026-12-24", "end_date": "2026-12-24"}


app.dependency_overrides[get_current_user] = lambda: U()
app.dependency_overrides[require_event_access] = lambda event_id: U()
c = TestClient(app)


def seed(status, subtotal, discount, fee, net):
    return Order(
        event_id=EV, organization_id=ORG, status=status, buyer_name="B",
        buyer_email=f"{uuid.uuid4().hex[:8]}@x.com", currency="usd",
        subtotal_cents=subtotal, discount_cents=discount,
        platform_fee_cents=fee, organizer_net_cents=net,
        order_token="T" + uuid.uuid4().hex[:10].upper(),
    )


db = SessionLocal()
db.add_all([
    # two paid: $100 clean, and $60 with a $10 discount (charged 50, fee 225? no —
    # fee numbers here are FROZEN SNAPSHOTS, the endpoint must sum them as-is)
    seed(OrderStatus.PAID, 10000, 0, 375, 9625),
    seed(OrderStatus.PAID, 6000, 1000, 225, 4775),
    # one $0 comp: counts as a paid order, moves no money
    seed(OrderStatus.PAID, 0, 0, 0, 0),
    # one refunded $55: gross returned, fee returned -> feeds refunded only
    seed(OrderStatus.REFUNDED, 5500, 0, 240, 5260),
    # noise that must count NOWHERE
    seed(OrderStatus.PENDING, 9900, 0, 372, 9528),
    seed(OrderStatus.EXPIRED, 8800, 0, 339, 8461),
])
db.commit(); db.close()

print("1) earnings sums the money states")
e = c.get(f"/events/{EV}/payments/earnings").json()
check("gross = paid charged (100 + 50 + 0)", e["gross_sold_cents"] == 15000, str(e))
check("fees = paid snapshots only", e["platform_fees_cents"] == 600, str(e))
check("net = paid snapshots only", e["organizer_net_cents"] == 14400, str(e))
check("refunded = refunded charged", e["refunded_cents"] == 5500, str(e))
check("pending/expired count nowhere", e["gross_sold_cents"] + e["refunded_cents"] == 20500)

print("2) the $0 comp shows in counts, not money")
check("3 paid orders, 1 refunded", e["paid_orders"] == 3 and e["refunded_orders"] == 1, str(e))

print("3) an orderless event returns zeros")
e0 = c.get(f"/events/{EV_EMPTY}/payments/earnings").json()
check("all zero, usd default", e0["gross_sold_cents"] == 0 and e0["paid_orders"] == 0 and e0["currency"] == "usd", str(e0))

print("4) payouts with no enabled account: normal, empty")
p = c.get(f"/events/{EV}/payments/payouts")
check("200, connected=false", p.status_code == 200 and p.json()["connected"] is False, p.text)

print("5) payouts once connected")
db = SessionLocal()
db.add(PaymentAccount(organization_id=ORG, stripe_account_id=ACCT,
                      charges_enabled=True, payouts_enabled=True, details_submitted=True))
db.commit(); db.close()
gateway.retrieve_balance = lambda acct: {
    "available": [{"amount": 4000, "currency": "usd"}, {"amount": 999, "currency": "eur"}],
    "pending": [{"amount": 6595, "currency": "usd"}],
}
gateway.list_payouts = lambda acct, limit=10: {
    "data": [{"amount": 2500, "currency": "usd", "status": "paid", "arrival_date": 1788220800}],
}
p = c.get(f"/events/{EV}/payments/payouts").json()
check("connected", p["connected"] is True, str(p))
check("usd-only balance sums (eur ignored)", p["balance_available_cents"] == 4000 and p["balance_pending_cents"] == 6595, str(p))
check("payout with ISO arrival date", p["payouts"] == [{"amount_cents": 2500, "currency": "usd", "status": "paid", "arrival_date": "2026-09-01"}], str(p))

# ---- cleanup ----
db = SessionLocal()
db.query(Order).filter(Order.event_id == EV).delete()
db.query(PaymentAccount).filter(PaymentAccount.organization_id == ORG).delete()
db.commit(); db.close()

print()
if failures:
    print(f"FAILED: {len(failures)} — " + ", ".join(failures))
    sys.exit(1)
print("test_earnings: all checks passed")