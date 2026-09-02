# eventnxt-backend: test/test_pass.py  (verification harness)
#
# Derived all-days pass (slice 3) against a REAL Postgres:
#   1. creation guards: mixed span required; single-day family refused;
#      non-seated family refused; second active pass on a family refused
#   2. pass seat map = INTERSECTION: a seat blocked on one night is
#      unavailable to the pass
#   3. buying the pass mints one dated code per night, same seat number,
#      and takes that seat on every night's own map
#   4. a single-night buyer is refused the pass-held seat; a second pass
#      wants a different seat
#   5. pass quantity cap enforced
#   6. pass buyer vs single-night buyer racing for the same chair:
#      exactly one winner (real threads)
#   7. deleting a member night is refused while the pass exists
#
# Run: DATABASE_URL="postgresql://test@/eventnxt_test?host=/tmp&port=5433" python3 test/test_pass.py
import os
import sys
from pathlib import Path

# Runs from the repo root (python3 test/<file>.py) or from inside
# test/ — either way, make the repo root importable for `app`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import threading
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
TT_BASE = {"description": None, "price_cents": 0, "max_per_order": 10, "admits": 1,
           "sales_start": None, "sales_end": None, "is_active": True, "sort_order": 0}


def buy(slug, tt_id, seat_ids, name="B"):
    return client.post(
        f"/public/events/{slug}/checkout",
        json={"buyer_name": name, "buyer_email": f"{name.lower()}@x.com",
              "items": [{"ticket_type_id": tt_id, "quantity": len(seat_ids), "seat_ids": seat_ids}]},
    )


def main():
    client.patch(f"/events/{EV}/settings", json={"ticket_span": "per_day"}, headers=H)
    pool = client.post(f"/events/{EV}/seating-categories",
                       json={"name": "Row 1 Premium", "capacity": 1, "sales_grain": "seat", "row_label": "Row 1"},
                       headers=H).json()
    client.put(f"/events/{EV}/seating-categories/{pool['id']}/sections",
               json={"sections": [{"section_label": "A", "row_label": "Row 1", "capacity": 4}]}, headers=H)
    tt = client.post(f"/events/{EV}/ticket-types",
                     json={**TT_BASE, "name": "Row 1 Premium", "quantity": 4, "seating_category_id": pool["id"], "valid_date": D1},
                     headers=H).json()
    client.post(f"/events/{EV}/ticket-types/{tt['id']}/fan-out", headers=H)
    client.patch(f"/events/{EV}/profile/refund-policy", json={"refund_policy": "none"}, headers=H)
    slug = client.post(f"/events/{EV}/profile/publish", headers=H).json()["slug"]

    # ---- 1. creation guards ----
    r = client.post(f"/events/{EV}/ticket-types/{tt['id']}/pass",
                    json={"name": "Weekend Row 1", "price_cents": 0, "quantity": 2}, headers=H)
    check("pass refused outside mixed span", r.status_code == 400 and "mixed" in r.text, r.text)
    client.patch(f"/events/{EV}/settings", json={"ticket_span": "mixed"}, headers=H)
    ga = client.post(f"/events/{EV}/ticket-types",
                     json={**TT_BASE, "name": "GA", "quantity": 50, "seating_category_id": None, "valid_date": D1},
                     headers=H).json()
    r = client.post(f"/events/{EV}/ticket-types/{ga['id']}/pass",
                    json={"name": "GA Pass", "price_cents": 0, "quantity": 5}, headers=H)
    check("single-day / non-seated family refused", r.status_code == 400, r.text)
    r = client.post(f"/events/{EV}/ticket-types/{tt['id']}/pass",
                    json={"name": "Weekend Row 1", "price_cents": 0, "quantity": 2, "max_per_order": 2}, headers=H)
    check("pass created from the seated family", r.status_code == 201 and r.json()["valid_date"] is None, r.text)
    pas = r.json()
    r = client.post(f"/events/{EV}/ticket-types/{tt['id']}/pass",
                    json={"name": "Another", "price_cents": 0, "quantity": 1}, headers=H)
    check("second active pass on the family refused", r.status_code == 400, r.text)

    # ---- public listing: pass is a seat-picked all-days product ----
    pub = client.get(f"/public/events/{slug}/ticket-types").json()
    p = next(x for x in pub if x["id"] == pas["id"])
    check("listing: pass is assigned_seating with no date", p["assigned_seating"] is True and p["valid_date"] is None, p)

    # ---- 2. intersection map ----
    fri_seats = client.get(f"/events/{EV}/seating-categories/{pool['id']}/seats", headers=H).json()
    by_num = {s["seat_number"]: s for s in fri_seats}
    client.post(f"/events/{EV}/seating-categories/{pool['id']}/seats/block",
                json={"seat_ids": [by_num[1]["id"]], "label": "Press Fri"}, headers=H)
    pmap = client.get(f"/public/events/{slug}/ticket-types/{pas['id']}/seats").json()
    pavail = {x["seat_number"]: x["available"] for sec in pmap["sections"] for x in sec["seats"]}
    check("seat blocked one night is unavailable to the pass", pavail == {1: False, 2: True, 3: True, 4: True}, pavail)

    # ---- 3. buy the pass: 3 dated codes, same seat, every night claimed ----
    pass_seat2 = next(x for sec in pmap["sections"] for x in sec["seats"] if x["seat_number"] == 2)
    r = buy(slug, pas["id"], [pass_seat2["id"]], name="P")
    check("pass checkout ok", r.status_code == 200, r.text)
    order = client.get(f"/public/orders/{r.json()['order_token']}").json()
    dates = sorted(t["valid_date"] for t in order["tickets"])
    seats_shown = {t["seat_label"] for t in order["tickets"]}
    check("3 dated codes, one per night", dates == [D1, D2, D3], order["tickets"])
    check("every code carries Seat 2", len(seats_shown) == 1 and "Seat 2" in list(seats_shown)[0], seats_shown)
    fri_map = client.get(f"/public/events/{slug}/ticket-types/{tt['id']}/seats").json()
    fri_avail = {x["seat_number"]: x["available"] for sec in fri_map["sections"] for x in sec["seats"]}
    check("friday's own map shows seat 2 gone", fri_avail[2] is False, fri_avail)

    # ---- 4. single-night buyer refused the pass seat ----
    r = buy(slug, tt["id"], [by_num[2]["id"]], name="S")
    check("single-night buyer refused seat 2", r.status_code == 400, r.text)

    # ---- 5. cap ----
    pmap = client.get(f"/public/events/{slug}/ticket-types/{pas['id']}/seats").json()
    ids = {x["seat_number"]: x["id"] for sec in pmap["sections"] for x in sec["seats"]}
    r = buy(slug, pas["id"], [ids[3]], name="Q")
    check("second pass sells (cap 2)", r.status_code == 200, r.text)
    r = buy(slug, pas["id"], [ids[4]], name="R")
    check("third pass refused by cap", r.status_code == 400, r.text)

    # ---- 6. race: pass vs single-night for the last free chair (seat 4) ----
    results = []
    def pass_buyer():
        results.append(("pass", buy(slug, pas["id"], [ids[4]], name="RaceP").status_code))
    def single_buyer():
        results.append(("single", buy(slug, tt["id"], [by_num[4]["id"]], name="RaceS").status_code))
    # cap is exhausted for the pass — bump it so the race is about the seat
    client.put(f"/events/{EV}/ticket-types/{pas['id']}",
               json={**TT_BASE, "name": pas["name"], "quantity": 5, "max_per_order": 2, "seating_category_id": None},
               headers=H)
    ts = [threading.Thread(target=pass_buyer), threading.Thread(target=single_buyer)]
    for t in ts: t.start()
    for t in ts: t.join()
    winners = sum(1 for (_, code) in results if code == 200)
    check("race: exactly one winner", winners == 1, results)

    # ---- 7. member delete guard ----
    r = client.delete(f"/events/{EV}/ticket-types/{tt['id']}", headers=H)
    check("member night can't be deleted under a pass", r.status_code == 400 and "pass" in r.text, r.text)

    print()
    if failures:
        print("FAILURES:", failures)
        raise SystemExit(1)
    print("derived pass: all clear")


if __name__ == "__main__":
    main()