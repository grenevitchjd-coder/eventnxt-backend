# eventnxt-backend: test_reserved_seats.py  (verification harness — repo root)
#
# Reserved seats (Slice A) against a REAL Postgres:
#   1. seat view: statuses derive correctly
#   2. block 3-5 "Press" -> reserved w/ label; public map shows unavailable
#   3. public checkout of a reserved seat -> refused; free seat -> mints
#   4. blocking a sold seat -> refused
#   5. release -> seat purchasable again
#   6. section restructure that would delete a reserved seat -> 400
#   7. admin/buyer race on the same seat -> exactly one winner (threads)
#
# Run: DATABASE_URL="postgresql://test@/eventnxt_test?host=/tmp&port=5433" python3 test_reserved_seats.py
import os
import threading
import uuid

os.environ.setdefault("DATABASE_URL", "postgresql://test@/eventnxt_test?host=/tmp&port=5433")

from fastapi.testclient import TestClient

from app.main import app
from app.services.deps import get_current_user
from app.services.event_access import require_event_access

EV = str(uuid.uuid4())
failures = []


def check(name, cond, extra=""):
    print(("  ok " if cond else "  ✗ ") + name + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


ORG_ID = str(uuid.uuid4())


class FakeUser:
    user_id = "u-1"
    organization_id = ORG_ID
    name = "Test Owner"
    email = "o@example.com"
    role = "owner"
    raw_token = "tok"
    event_data = {"organization_id": ORG_ID, "name": "Runway Test", "start_date": None, "end_date": None}


app.dependency_overrides[get_current_user] = lambda: FakeUser()
app.dependency_overrides[require_event_access] = lambda event_id: FakeUser()
client = TestClient(app)
H = {"Authorization": "Bearer tok"}


def main():
    # ---- bootstrap: pool w/ sections, ticket type, published page ----
    pool = client.post(
        f"/events/{EV}/seating-categories",
        json={"name": "Row 1", "capacity": 1, "sales_grain": "seat", "row_label": "Row 1"},
        headers=H,
    ).json()
    client.put(
        f"/events/{EV}/seating-categories/{pool['id']}/sections",
        json={"sections": [{"section_label": "A", "row_label": "Row 1", "capacity": 6}]},
        headers=H,
    )
    tt = client.post(
        f"/events/{EV}/ticket-types",
        json={
            "name": "Row 1", "description": None, "price_cents": 0, "quantity": 6,
            "max_per_order": 6, "admits": 1, "seating_category_id": pool["id"],
            "sales_start": None, "sales_end": None, "is_active": True, "sort_order": 0,
        },
        headers=H,
    ).json()
    client.patch(f"/events/{EV}/profile/refund-policy", json={"refund_policy": "none"}, headers=H)
    slug = client.post(f"/events/{EV}/profile/publish", headers=H).json()["slug"]

    # ---- 1. seat view ----
    seats = client.get(f"/events/{EV}/seating-categories/{pool['id']}/seats", headers=H).json()
    check("6 seats generated, all available", len(seats) == 6 and all(s["status"] == "available" for s in seats))
    by_num = {s["seat_number"]: s for s in seats}

    # ---- 2. reserve 3-5 for Press ----
    press_ids = [by_num[n]["id"] for n in (3, 4, 5)]
    view = client.post(
        f"/events/{EV}/seating-categories/{pool['id']}/seats/block",
        json={"seat_ids": press_ids, "label": "Press"},
        headers=H,
    ).json()
    vb = {s["seat_number"]: s for s in view}
    check(
        "3-5 reserved with label, others untouched",
        all(vb[n]["status"] == "reserved" and vb[n]["block_label"] == "Press" for n in (3, 4, 5))
        and all(vb[n]["status"] == "available" for n in (1, 2, 6)),
    )
    pub = client.get(f"/public/events/{slug}/ticket-types/{tt['id']}/seats").json()
    pub_avail = {x["seat_number"]: x["available"] for sec in pub["sections"] for x in sec["seats"]}
    check("public map: 3-5 unavailable, 1,2,6 open", pub_avail == {1: True, 2: True, 3: False, 4: False, 5: False, 6: True})

    # ---- 3. checkout: reserved refused, free mints ----
    r = client.post(
        f"/public/events/{slug}/checkout",
        json={"buyer_name": "B", "buyer_email": "b@x.com",
              "items": [{"ticket_type_id": tt["id"], "quantity": 1, "seat_ids": [by_num[3]["id"]]}]},
    )
    check("checkout of reserved seat refused", r.status_code == 400, r.text)
    r = client.post(
        f"/public/events/{slug}/checkout",
        json={"buyer_name": "B", "buyer_email": "b@x.com",
              "items": [{"ticket_type_id": tt["id"], "quantity": 1, "seat_ids": [by_num[1]["id"]]}]},
    )
    check("free seat 1 checkout mints ($0 sync)", r.status_code == 200, r.text)

    # ---- 4. blocking a sold seat refused ----
    r = client.post(
        f"/events/{EV}/seating-categories/{pool['id']}/seats/block",
        json={"seat_ids": [by_num[1]["id"]], "label": "Oops"},
        headers=H,
    )
    check("blocking sold seat refused", r.status_code == 400, r.text)
    view = client.get(f"/events/{EV}/seating-categories/{pool['id']}/seats", headers=H).json()
    check("sold seat shows sold", {s["seat_number"]: s["status"] for s in view}[1] == "sold")

    # ---- 5. release -> purchasable ----
    client.post(
        f"/events/{EV}/seating-categories/{pool['id']}/seats/unblock",
        json={"seat_ids": [by_num[3]["id"]]},
        headers=H,
    )
    r = client.post(
        f"/public/events/{slug}/checkout",
        json={"buyer_name": "C", "buyer_email": "c@x.com",
              "items": [{"ticket_type_id": tt["id"], "quantity": 1, "seat_ids": [by_num[3]["id"]]}]},
    )
    check("released seat 3 purchasable", r.status_code == 200, r.text)

    # ---- 6. restructure protecting reserved seats ----
    r = client.put(
        f"/events/{EV}/seating-categories/{pool['id']}/sections",
        json={"sections": [{"section_label": "A", "row_label": "Row 1", "capacity": 4}]},  # would delete 5 & 6; 5 is reserved
        headers=H,
    )
    check("shrink deleting reserved seat 5 refused", r.status_code == 400 and "reserved" in r.text, r.text)
    r = client.put(
        f"/events/{EV}/seating-categories/{pool['id']}/sections",
        json={"sections": [{"section_label": "A", "row_label": "Row 1", "capacity": 5}]},  # deletes only free seat 6
        headers=H,
    )
    check("shrink deleting only free seat 6 ok", r.status_code == 200, r.text)

    # ---- 7. admin block vs buyer checkout race on seat 2 ----
    results = []
    sid2 = by_num[2]["id"]

    def buyer():
        rr = client.post(
            f"/public/events/{slug}/checkout",
            json={"buyer_name": "R", "buyer_email": "r@x.com",
                  "items": [{"ticket_type_id": tt["id"], "quantity": 1, "seat_ids": [sid2]}]},
        )
        results.append(("buy", rr.status_code))

    def admin():
        rr = client.post(
            f"/events/{EV}/seating-categories/{pool['id']}/seats/block",
            json={"seat_ids": [sid2], "label": "Press"},
            headers=H,
        )
        results.append(("block", rr.status_code))

    ts = [threading.Thread(target=buyer), threading.Thread(target=admin)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    winners = sum(1 for (_, code) in results if code == 200)
    view = client.get(f"/events/{EV}/seating-categories/{pool['id']}/seats", headers=H).json()
    s2 = {s["seat_number"]: s["status"] for s in view}[2]
    # Both succeeding is only legal if the block landed first and the buy
    # then failed — so: exactly one 200, and seat 2 ends sold XOR reserved.
    check("race: exactly one winner", winners == 1 and s2 in ("sold", "reserved"), f"{results} -> {s2}")

    print()
    if failures:
        print("FAILURES:", failures)
        raise SystemExit(1)
    print("reserved seats: all clear")


if __name__ == "__main__":
    main()