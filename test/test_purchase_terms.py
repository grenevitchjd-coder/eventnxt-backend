# eventnxt-backend: test/test_purchase_terms.py  (verification harness)
#
# The Ticket Purchasing Agreement at checkout (migration 0048):
#   1. A checkout WITHOUT terms_accepted is a 400 — before any inventory
#      work, identically for a paid cart and a free cart, and no order
#      row persists.
#   2. With terms_accepted: the order stamps terms_accepted_at.
#   3. marketing_opt_in defaults false, stores true when sent, and is
#      exposed on the organizer's AdminOrderResponse.
#
# Run: DATABASE_URL="postgresql://test@/eventnxt_test?host=/tmp&port=5433" python3 test/test_purchase_terms.py
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uuid
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql://test@/eventnxt_test?host=/tmp&port=5433")

from fastapi.testclient import TestClient

import app.services.email as email_mod
from app.database import SessionLocal
from app.main import app
from app.models.order import Order
from app.services.deps import get_current_user
from app.services.event_access import require_event_access
import app.routers.checkout as checkout_router

email_mod.send_email = lambda **kw: None

EV, ORG = str(uuid.uuid4()), str(uuid.uuid4())
failures = []


def check(name, cond, extra=""):
    print(("  ok " if cond else "  ✗ ") + name + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


class U:
    user_id = "u"; organization_id = ORG; name = "T"; email = "t@x.com"; role = "owner"; raw_token = "tok"
    event_data = {"organization_id": ORG, "name": "Terms", "start_date": "2026-12-24", "end_date": "2026-12-24"}


app.dependency_overrides[get_current_user] = lambda: U()
app.dependency_overrides[require_event_access] = lambda event_id: U()
c = TestClient(app)
H = {"Authorization": "Bearer tok"}

checkout_router.create_checkout_session = lambda *a, **kw: SimpleNamespace(
    id=f"cs_test_{uuid.uuid4().hex}", url="https://checkout.stripe.com/test"
)

TT = {"description": None, "max_per_order": 20, "admits": 1, "sales_start": None, "sales_end": None,
      "is_active": True, "sort_order": 0, "valid_date": None}


def order_by_email(email):
    db = SessionLocal()
    o = db.query(Order).filter(Order.event_id == EV, Order.buyer_email == email).first()
    if o:
        db.expunge(o)
    db.close()
    return o


def main():
    c.patch(f"/events/{EV}/settings", json={"ticket_span": "whole_event", "ticketing_mode": "native"}, headers=H)
    pool = c.post(f"/events/{EV}/seating-categories",
                  json={"name": "GA", "capacity": 50, "sales_grain": "ga", "row_label": None}, headers=H).json()
    paid_t = c.post(f"/events/{EV}/ticket-types",
                    json={**TT, "name": "GA", "price_cents": 2000, "quantity": 20,
                          "seating_category_id": pool["id"]}, headers=H).json()
    free_t = c.post(f"/events/{EV}/ticket-types",
                    json={**TT, "name": "Free", "price_cents": 0, "quantity": 20,
                          "seating_category_id": pool["id"]}, headers=H).json()
    c.patch(f"/events/{EV}/profile/refund-policy", json={"refund_policy": "none"}, headers=H)
    slug = c.post(f"/events/{EV}/profile/publish", headers=H).json()["slug"]

    def buy(email, tt, terms=None, marketing=None):
        body = {"buyer_name": "B", "buyer_email": email,
                "items": [{"ticket_type_id": tt["id"], "quantity": 1,
                           "seat_ids": [], "zone_section_id": None, "zone_section_ids": []}]}
        if terms is not None:
            body["terms_accepted"] = terms
        if marketing is not None:
            body["marketing_opt_in"] = marketing
        return c.post(f"/public/events/{slug}/checkout", json=body)

    print("1) no agreement -> 400, nothing persists")
    r = buy("noterms@x.com", paid_t)
    check("paid cart without terms: 400", r.status_code == 400 and "Purchasing Agreement" in r.json()["detail"], r.text[:140])
    r = buy("nofree@x.com", free_t, terms=False)
    check("free cart without terms: same 400", r.status_code == 400 and "Purchasing Agreement" in r.json()["detail"], r.text[:140])
    check("no order rows persisted", order_by_email("noterms@x.com") is None and order_by_email("nofree@x.com") is None)

    print("2) agreed checkout stamps acceptance")
    r = buy("agree@x.com", paid_t, terms=True)
    check("checkout 200", r.status_code == 200, r.text[:140])
    o = order_by_email("agree@x.com")
    check("terms_accepted_at stamped", o is not None and o.terms_accepted_at is not None)
    check("marketing defaults false", o.marketing_opt_in is False)

    print("3) marketing opt-in stores and surfaces to the organizer")
    r = buy("optin@x.com", free_t, terms=True, marketing=True)
    check("free agreed checkout 200 paid-now", r.status_code == 200 and r.json()["status"] == "paid", r.text[:140])
    o = order_by_email("optin@x.com")
    check("marketing_opt_in stored true (and free path stamps terms too)",
          o.marketing_opt_in is True and o.terms_accepted_at is not None)
    orders = c.get(f"/events/{EV}/orders", headers=H).json()
    by_email = {x["buyer_email"]: x for x in orders}
    check("admin response exposes the consent",
          by_email["optin@x.com"]["marketing_opt_in"] is True and by_email["agree@x.com"]["marketing_opt_in"] is False,
          str({k: v.get("marketing_opt_in") for k, v in by_email.items()}))


main()
print()
if failures:
    print(f"FAILED: {len(failures)} — " + ", ".join(failures))
    sys.exit(1)
print("test_purchase_terms: all checks passed")