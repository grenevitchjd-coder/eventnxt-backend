# eventnxt-backend: test/test_perday.py  (verification harness)
#
# Per-day selling (slice 2) against a REAL Postgres:
#   1. span validation: per_day requires a day on every type; dated types
#      refused outside per_day/mixed; day must be one of the event's days
#   2. fan-out clones a dated seated template to every remaining day —
#      per-day pools with identical sections and fresh seats
#   3. fan-out is idempotent (rerun creates nothing)
#   4. inventories are independent: selling Friday seat 1 leaves
#      Saturday seat 1 available
#   5. a dated purchase mints ONE code stamped to its day (no fan-out of
#      codes for dated types)
#   6. mixed span sells dated types and a whole-event package side by
#      side; the public listing carries valid_date for grouping
#
# Run: DATABASE_URL="postgresql://test@/eventnxt_test?host=/tmp&port=5433" python3 test/test_perday.py
import os
import sys
from pathlib import Path

# Runs from the repo root (python3 test/<file>.py) or from inside
# test/ — either way, make the repo root importable for `app`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uuid

os.environ.setdefault("DATABASE_URL", "postgresql://test@/eventnxt_test?host=/tmp&port=5433")

from fastapi.testclient import TestClient

from app.main import app
from app.services.deps import get_current_user
from app.services.event_access import require_event_access

EV = str(uuid.uuid4())
ORG_ID = str(uuid.uuid4())
D1, D2, D3 = "2026-10-09", "2026-10-10", "2026-10-11"
failures = []


def check(name, cond, extra=""):
    print(("  ok " if cond else "  ✗ ") + name + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


class FakeUser:
    user_id = "u-1"
    organization_id = ORG_ID
    name = "Test Owner"
    email = "o@example.com"
    role = "owner"
    raw_token = "tok"
    event_data = {"organization_id": ORG_ID, "name": "Fest", "start_date": D1, "end_date": D3}


app.dependency_overrides[get_current_user] = lambda: FakeUser()
app.dependency_overrides[require_event_access] = lambda event_id: FakeUser()
client = TestClient(app)
H = {"Authorization": "Bearer tok"}

TT_BASE = {
    "description": None, "price_cents": 0, "max_per_order": 10, "admits": 1,
    "sales_start": None, "sales_end": None, "is_active": True, "sort_order": 0,
}


def main():
    # ---- 1. span/date validation ----
    r = client.post(f"/events/{EV}/ticket-types",
                    json={**TT_BASE, "name": "GA", "quantity": 10, "seating_category_id": None, "valid_date": D1},
                    headers=H)
    check("dated type refused before span set", r.status_code == 400, r.text)
    client.patch(f"/events/{EV}/settings", json={"ticket_span": "per_day"}, headers=H)
    r = client.post(f"/events/{EV}/ticket-types",
                    json={**TT_BASE, "name": "GA", "quantity": 10, "seating_category_id": None},
                    headers=H)
    check("per_day span requires a day", r.status_code == 400 and "pick which day" in r.text, r.text)
    r = client.post(f"/events/{EV}/ticket-types",
                    json={**TT_BASE, "name": "GA", "quantity": 10, "seating_category_id": None, "valid_date": "2026-12-25"},
                    headers=H)
    check("day outside the event refused", r.status_code == 400, r.text)

    # ---- 2. fan-out of a seated Friday template ----
    pool = client.post(f"/events/{EV}/seating-categories",
                       json={"name": "Row 1", "capacity": 1, "sales_grain": "seat", "row_label": "Row 1"},
                       headers=H).json()
    client.put(f"/events/{EV}/seating-categories/{pool['id']}/sections",
               json={"sections": [{"section_label": "A", "row_label": "Row 1", "capacity": 3}]}, headers=H)
    tt = client.post(f"/events/{EV}/ticket-types",
                     json={**TT_BASE, "name": "Row 1", "quantity": 3, "seating_category_id": pool["id"], "valid_date": D1},
                     headers=H).json()
    r = client.post(f"/events/{EV}/ticket-types/{tt['id']}/fan-out", headers=H)
    check("fan-out creates the two missing days", r.status_code == 200 and sorted(x["valid_date"] for x in r.json()) == [D2, D3], r.text)
    clones = {x["valid_date"]: x for x in r.json()}
    pools = client.get(f"/events/{EV}/seating-categories", headers=H).json()
    names = sorted(p["name"] for p in pools)
    check("per-day pools cloned with day-tagged names",
          "Row 1 (10/10)" in names and "Row 1 (10/11)" in names, names)
    sat_seats = client.get(f"/events/{EV}/seating-categories/{clones[D2]['seating_category_id']}/seats", headers=H).json()
    check("clone has fresh seats matching sections", len(sat_seats) == 3 and all(s["status"] == "available" for s in sat_seats), sat_seats)

    # ---- 3. idempotent ----
    r = client.post(f"/events/{EV}/ticket-types/{tt['id']}/fan-out", headers=H)
    check("rerun creates nothing", r.status_code == 200 and r.json() == [], r.text)

    # ---- 4 + 5. independent inventories, single dated code ----
    client.patch(f"/events/{EV}/profile/refund-policy", json={"refund_policy": "none"}, headers=H)
    slug = client.post(f"/events/{EV}/profile/publish", headers=H).json()["slug"]
    fri_seats = client.get(f"/events/{EV}/seating-categories/{pool['id']}/seats", headers=H).json()
    r = client.post(f"/public/events/{slug}/checkout",
                    json={"buyer_name": "F", "buyer_email": "f@x.com",
                          "items": [{"ticket_type_id": tt["id"], "quantity": 1, "seat_ids": [fri_seats[0]["id"]]}]})
    check("friday seat 1 sells", r.status_code == 200, r.text)
    order = client.get(f"/public/orders/{r.json()['order_token']}").json()
    check("dated type mints exactly one code, stamped its day",
          len(order["tickets"]) == 1 and order["tickets"][0]["valid_date"] == D1, order["tickets"])
    sat_seats = client.get(f"/events/{EV}/seating-categories/{clones[D2]['seating_category_id']}/seats", headers=H).json()
    check("saturday seat 1 untouched", all(s["status"] == "available" for s in sat_seats), sat_seats)

    # ---- 6. mixed: package alongside days; public listing groups ----
    client.patch(f"/events/{EV}/settings", json={"ticket_span": "mixed"}, headers=H)
    pkg = client.post(f"/events/{EV}/ticket-types",
                      json={**TT_BASE, "name": "Weekend Package", "quantity": 5, "seating_category_id": None},
                      headers=H)
    check("mixed allows a whole-event package", pkg.status_code == 201 and pkg.json()["valid_date"] is None, pkg.text)
    pub = client.get(f"/public/events/{slug}/ticket-types").json()
    by_name = {}
    for t in pub:
        by_name.setdefault(t["name"], []).append(t["valid_date"])
    check("public listing carries dates for grouping",
          sorted(by_name.get("Row 1", [])) == [D1, D2, D3] and by_name.get("Weekend Package") == [None], by_name)
    r = client.post(f"/public/events/{slug}/checkout",
                    json={"buyer_name": "P", "buyer_email": "p@x.com",
                          "items": [{"ticket_type_id": pkg.json()["id"], "quantity": 1}]})
    order = client.get(f"/public/orders/{r.json()['order_token']}").json()
    check("package mints one dated code per day",
          sorted(t["valid_date"] for t in order["tickets"]) == [D1, D2, D3], order["tickets"])

    print()
    if failures:
        print("FAILURES:", failures)
        raise SystemExit(1)
    print("per-day slice 2: all clear")


if __name__ == "__main__":
    main()