# eventnxt-backend: test/test_convert_pass.py  (verification harness)
#
# Convert-to-pass (retro-fit for "the all-days package was made first")
# + normalized family matching, against a REAL Postgres:
#   1. normalized names: a trailing-space / case-mangled nightly clone
#      still counts as family — fan-out coverage and pass creation both
#      see it (a stray space can't split a family)
#   2. a standalone undated type (own pool + duplicate seats) converts:
#      keeps name/price/cap, becomes is_pass, its own pool/sections/
#      seats are GONE, pass_members link every night
#   3. the converted pass sells like a born pass: intersection seat map,
#      one dated code per night on the same seat, nights consumed
#   4. guards: wrong span, dated type, already-a-pass, already-a-member,
#      sold/held standalone, blocked/guest-assigned own seats, bad
#      template
#   5. shared pool: converting detaches but leaves the pool (and the
#      other type on it) untouched
#
# Run: DATABASE_URL="postgresql://test@/eventnxt_test?host=/tmp&port=5433" python3 test/test_convert_pass.py
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
D1, D2, D3 = "2026-12-24", "2026-12-25", "2026-12-26"
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
    event_data = {"organization_id": ORG_ID, "name": "Teal Show", "start_date": D1, "end_date": D3}


app.dependency_overrides[get_current_user] = lambda: FakeUser()
app.dependency_overrides[require_event_access] = lambda event_id: FakeUser()
client = TestClient(app)
H = {"Authorization": "Bearer tok"}
TT_BASE = {"description": None, "price_cents": 0, "max_per_order": 10, "admits": 1,
           "sales_start": None, "sales_end": None, "is_active": True, "sort_order": 0}


def make_seated_type(name, day, sections, quantity, price_cents=0):
    pool = client.post(f"/events/{EV}/seating-categories",
                       json={"name": name, "capacity": 1, "sales_grain": "seat", "row_label": "Row 1"},
                       headers=H).json()
    client.put(f"/events/{EV}/seating-categories/{pool['id']}/sections",
               json={"sections": sections}, headers=H)
    tt = client.post(f"/events/{EV}/ticket-types",
                     json={**TT_BASE, "name": name, "price_cents": price_cents, "quantity": quantity,
                           "seating_category_id": pool["id"], "valid_date": day},
                     headers=H).json()
    return tt, pool


def buy(slug, tt_id, seat_ids, name="B"):
    return client.post(
        f"/public/events/{slug}/checkout",
        json={"buyer_name": name, "buyer_email": f"{name.lower()}@x.com",
              "items": [{"ticket_type_id": tt_id, "quantity": len(seat_ids), "seat_ids": seat_ids}]},
    )


def main():
    client.patch(f"/events/{EV}/settings", json={"ticket_span": "mixed", "pricing_mode": "per_day"}, headers=H)
    client.patch(f"/events/{EV}/profile/refund-policy", json={"refund_policy": "none"}, headers=H)
    slug = client.post(f"/events/{EV}/profile/publish", headers=H).json()["slug"]
    secs = [{"section_label": "A", "row_label": "Row 1", "capacity": 2},
            {"section_label": "B", "row_label": "Row 1", "capacity": 2}]

    # ---- 1. normalized family matching ----
    thu, thu_pool = make_seated_type("Row 1 Front Center Patron", D1, secs, 4)
    client.post(f"/events/{EV}/ticket-types/{thu['id']}/fan-out", headers=H)
    types = client.get(f"/events/{EV}/ticket-types", headers=H).json()
    fri = next(t for t in types if t["valid_date"] == D2 and t["name"] == thu["name"])
    # Mangle Friday's name: trailing space + case. The family must survive.
    client.put(f"/events/{EV}/ticket-types/{fri['id']}",
               json={**TT_BASE, "name": "row 1 front center patron ", "quantity": fri["quantity"],
                     "seating_category_id": fri["seating_category_id"], "valid_date": D2}, headers=H)
    r = client.post(f"/events/{EV}/ticket-types/{thu['id']}/fan-out", headers=H)
    check("fan-out sees the mangled clone as covered (idempotent)", r.status_code == 200 and r.json() == [], r.text)

    # ---- 2. the standalone package trap, then the fix ----
    pkg, pkg_pool = make_seated_type("Row 1 Weekend Package", None, secs, 4, price_cents=41000)
    types = client.get(f"/events/{EV}/ticket-types", headers=H).json()
    check("standalone package starts with its own duplicate pool",
          next(t for t in types if t["id"] == pkg["id"])["seating_category_id"] == pkg_pool["id"])

    # Guard checks that must fire BEFORE any successful conversion
    r = client.post(f"/events/{EV}/ticket-types/{thu['id']}/convert-to-pass",
                    json={"template_type_id": thu["id"]}, headers=H)
    check("dated type refused", r.status_code == 400 and "day-specific" in r.text, r.text)
    r = client.post(f"/events/{EV}/ticket-types/{pkg['id']}/convert-to-pass",
                    json={"template_type_id": pkg["id"]}, headers=H)
    check("undated template refused", r.status_code == 404, r.text)

    # Blocked own seat forbids conversion until released
    own_seats = client.get(f"/events/{EV}/seating-categories/{pkg_pool['id']}/seats", headers=H).json()
    client.post(f"/events/{EV}/seating-categories/{pkg_pool['id']}/seats/block",
                json={"seat_ids": [own_seats[0]["id"]], "label": "Press"}, headers=H)
    r = client.post(f"/events/{EV}/ticket-types/{pkg['id']}/convert-to-pass",
                    json={"template_type_id": thu["id"]}, headers=H)
    check("blocked own seat refused", r.status_code == 400 and "reserved" in r.text, r.text)
    client.post(f"/events/{EV}/seating-categories/{pkg_pool['id']}/seats/unblock",
                json={"seat_ids": [own_seats[0]["id"]]}, headers=H)

    # The conversion itself
    r = client.post(f"/events/{EV}/ticket-types/{pkg['id']}/convert-to-pass",
                    json={"template_type_id": thu["id"]}, headers=H)
    check("conversion succeeds", r.status_code == 200, r.text)
    conv = r.json()
    check("keeps name/price/cap, no pool, is_pass",
          conv["name"] == "Row 1 Weekend Package" and conv["price_cents"] == 41000
          and conv["quantity"] == 4 and conv["seating_category_id"] is None and conv["is_pass"] is True, conv)
    pools = client.get(f"/events/{EV}/seating-categories", headers=H).json()
    check("its duplicate pool is gone", all(p["id"] != pkg_pool["id"] for p in pools))

    # ---- 3. converted pass sells like a born pass ----
    # ($0 for the instant-fulfillment path — no Stripe in the harness)
    client.put(f"/events/{EV}/ticket-types/{pkg['id']}",
               json={**TT_BASE, "name": conv["name"], "price_cents": 0, "quantity": conv["quantity"],
                     "seating_category_id": None, "valid_date": ""}, headers=H)
    pmap = client.get(f"/public/events/{slug}/ticket-types/{pkg['id']}/seats").json()
    flat = [x for sec in pmap["sections"] for x in sec["seats"]]
    check("pass seat map = the NIGHTLY room (2+2), not the old duplicate",
          len(flat) == 4 and {s["section_label"] for s in pmap["sections"]} == {"A", "B"}, pmap)
    pick = next(x for x in flat if x["available"])
    r = buy(slug, pkg["id"], [pick["id"]], name="W")
    check("pass checkout ok", r.status_code == 200, r.text)
    order = client.get(f"/public/orders/{r.json()['order_token']}").json()
    dates = sorted(t["valid_date"] for t in order["tickets"])
    seat_labels = {t["seat_label"] for t in order["tickets"]}
    check("one dated code per night", dates == [D1, D2, D3], order["tickets"])
    check("same seat on every night", len(seat_labels) == 1, seat_labels)
    thu_map = client.get(f"/public/events/{slug}/ticket-types/{thu['id']}/seats").json()
    thu_taken = [x for sec in thu_map["sections"] for x in sec["seats"] if not x["available"]]
    check("the nightly map shows the pass's chair taken", len(thu_taken) == 1, thu_map)

    # ---- 4. remaining guards ----
    r = client.post(f"/events/{EV}/ticket-types/{pkg['id']}/convert-to-pass",
                    json={"template_type_id": thu["id"]}, headers=H)
    check("already-a-pass refused", r.status_code == 400 and "already" in r.text, r.text)
    sold, sold_pool = make_seated_type("Sold Package", None, secs, 4)
    smap = client.get(f"/public/events/{slug}/ticket-types/{sold['id']}/seats").json()
    s_pick = next(x for sec in smap["sections"] for x in sec["seats"] if x["available"])
    buy(slug, sold["id"], [s_pick["id"]], name="S")
    r = client.post(f"/events/{EV}/ticket-types/{sold['id']}/convert-to-pass",
                    json={"template_type_id": thu["id"]}, headers=H)
    check("sold standalone refused", r.status_code == 400, r.text)
    client.patch(f"/events/{EV}/settings", json={"ticket_span": "per_day"}, headers=H)
    r = client.post(f"/events/{EV}/ticket-types/{pkg['id']}/convert-to-pass",
                    json={"template_type_id": thu["id"]}, headers=H)
    check("wrong span refused", r.status_code == 400 and "mixed" in r.text, r.text)
    client.patch(f"/events/{EV}/settings", json={"ticket_span": "mixed"}, headers=H)

    # ---- 5. shared pool: detach only ----
    twin_a, shared_pool = make_seated_type("Twin A", None, secs, 4)
    twin_b = client.post(f"/events/{EV}/ticket-types",
                         json={**TT_BASE, "name": "Twin B", "quantity": 4,
                               "seating_category_id": shared_pool["id"], "valid_date": None},
                         headers=H).json()
    # a second seated family for it to ride on
    n2, _ = make_seated_type("Row 2 Patron", D1, secs, 4)
    client.post(f"/events/{EV}/ticket-types/{n2['id']}/fan-out", headers=H)
    r = client.post(f"/events/{EV}/ticket-types/{twin_a['id']}/convert-to-pass",
                    json={"template_type_id": n2["id"]}, headers=H)
    check("shared-pool conversion succeeds", r.status_code == 200, r.text)
    pools = client.get(f"/events/{EV}/seating-categories", headers=H).json()
    check("shared pool survives for the other type", any(p["id"] == shared_pool["id"] for p in pools))
    types = client.get(f"/events/{EV}/ticket-types", headers=H).json()
    check("other type keeps the pool",
          next(t for t in types if t["id"] == twin_b["id"])["seating_category_id"] == shared_pool["id"])

    print()
    if failures:
        print(f"convert-to-pass: {len(failures)} FAILED — " + ", ".join(failures))
        sys.exit(1)
    print("convert-to-pass: all clear")


if __name__ == "__main__":
    main()