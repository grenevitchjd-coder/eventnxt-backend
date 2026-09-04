# eventnxt-backend: test/test_capacity_drift.py  (verification harness)
#
# The capacity-drift fix: a bare GA pool created alongside its ticket
# type follows the type's quantity when edited.
#   1. Composer shape (GA pool cap 10 + type qty 10): edit qty → 14 →
#      pool capacity follows; a buyer can then actually claim past the
#      old 10 at checkout.
#   2. Lowering follows too (14 → 6).
#   3. SHARED pool (two types selling it): capacity does NOT move.
#   4. SEATED pool: capacity does NOT move (chairs are physical).
#   5. SECTIONED GA zone: capacity does NOT move (managed per-section).
#
# Run: DATABASE_URL="postgresql://test@/eventnxt_test?host=/tmp&port=5433" python3 test/test_capacity_drift.py
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uuid

os.environ.setdefault("DATABASE_URL", "postgresql://test@/eventnxt_test?host=/tmp&port=5433")

from fastapi.testclient import TestClient

import app.services.email as email_mod
from app.main import app
from app.services.deps import get_current_user
from app.services.event_access import require_event_access

email_mod.send_email = lambda **kw: None

EV, ORG = str(uuid.uuid4()), str(uuid.uuid4())
D1 = "2026-12-24"
failures = []


def check(name, cond, extra=""):
    print(("  ok " if cond else "  ✗ ") + name + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


class U:
    user_id = "u"; organization_id = ORG; name = "T"; email = "t@x.com"; role = "owner"; raw_token = "tok"
    event_data = {"organization_id": ORG, "name": "Drift", "start_date": D1, "end_date": D1}


app.dependency_overrides[get_current_user] = lambda: U()
app.dependency_overrides[require_event_access] = lambda event_id: U()
c = TestClient(app)
H = {"Authorization": "Bearer tok"}

TT = {"description": None, "max_per_order": 20, "admits": 1, "sales_start": None, "sales_end": None,
      "is_active": True, "sort_order": 0, "valid_date": None}


def make_pool(name, grain="ga", capacity=10):
    return c.post(f"/events/{EV}/seating-categories",
                  json={"name": name, "capacity": capacity, "sales_grain": grain,
                        "row_label": "Row 1" if grain == "seat" else None}, headers=H).json()


def make_type(name, pool_id, qty, price=1000):
    return c.post(f"/events/{EV}/ticket-types",
                  json={**TT, "name": name, "price_cents": price, "quantity": qty,
                        "seating_category_id": pool_id}, headers=H).json()


def edit_qty(t, qty):
    return c.put(f"/events/{EV}/ticket-types/{t['id']}",
                 json={**TT, "name": t["name"], "price_cents": t["price_cents"], "quantity": qty,
                       "seating_category_id": t["seating_category_id"]}, headers=H).json()


def pool_cap(pool_id):
    pools = c.get(f"/events/{EV}/seating-categories", headers=H).json()
    return next(p for p in pools if p["id"] == pool_id)["capacity"]


def main():
    c.patch(f"/events/{EV}/settings", json={"ticket_span": "whole_event", "ticketing_mode": "native"}, headers=H)

    # ---- 1 & 2: bare GA pool follows its sole type ----
    pool = make_pool("Standing")
    t = make_type("Standing GA", pool["id"], 10, price=0)
    t = edit_qty(t, 14)
    check("raising qty 10→14 raises the bare GA pool", pool_cap(pool["id"]) == 14, pool_cap(pool["id"]))
    # a real checkout can claim past the old cap of 10
    c.patch(f"/events/{EV}/profile/refund-policy", json={"refund_policy": "none"}, headers=H)
    slug = c.post(f"/events/{EV}/profile/publish", headers=H).json()["slug"]
    r = c.post(f"/public/events/{slug}/checkout",
               json={"buyer_name": "Buyer", "buyer_email": "b@x.com",
                     "items": [{"ticket_type_id": t["id"], "quantity": 12}]})
    check("checkout for 12 (past the old 10) is accepted", r.status_code == 200, f"{r.status_code} {r.text[:120]}")
    t = edit_qty(t, 6)
    check("lowering qty follows too (→6)", pool_cap(pool["id"]) == 6, pool_cap(pool["id"]))

    # ---- 3: shared pool untouched ----
    shared = make_pool("Shared Lawn", capacity=50)
    a = make_type("Lawn A", shared["id"], 20)
    make_type("Lawn B", shared["id"], 20)
    edit_qty(a, 40)
    check("shared pool capacity does NOT follow one type", pool_cap(shared["id"]) == 50, pool_cap(shared["id"]))

    # ---- 4: seated pool untouched ----
    seated = make_pool("Row 1", grain="seat", capacity=4)
    c.put(f"/events/{EV}/seating-categories/{seated['id']}/sections",
          json={"sections": [{"section_label": "A", "row_label": "Row 1", "capacity": 4}]}, headers=H)
    st = make_type("Row 1 Seats", seated["id"], 4)
    edit_qty(st, 9)
    check("seated pool capacity does NOT follow (chairs are physical)", pool_cap(seated["id"]) == 4, pool_cap(seated["id"]))

    # ---- 5: sectioned GA zone untouched ----
    zone = make_pool("Sectioned Zone", capacity=30)
    c.put(f"/events/{EV}/seating-categories/{zone['id']}/sections",
          json={"sections": [{"section_label": "C", "row_label": None, "capacity": 15},
                             {"section_label": "D", "row_label": None, "capacity": 15}]}, headers=H)
    zt = make_type("Zone GA", zone["id"], 30)
    edit_qty(zt, 44)
    check("sectioned zone capacity does NOT follow (managed per-section)", pool_cap(zone["id"]) == 30, pool_cap(zone["id"]))

    print()
    if failures:
        print(f"capacity drift: {len(failures)} FAILED — " + ", ".join(failures))
        sys.exit(1)
    print("capacity drift: all clear")


if __name__ == "__main__":
    main()