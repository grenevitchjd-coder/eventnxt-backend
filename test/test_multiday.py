# eventnxt-backend: test/test_multiday.py  (verification harness)
#
# Multi-day slice 1 against a REAL Postgres:
#   1. settings: span needs days; bad dates and reversed ranges refused
#   2. whole-event purchase at a 3-day event mints one dated code per day
#   3. admits N fans out to N codes PER day
#   4. door: right day admits, wrong day rejected WITHOUT consuming,
#      the code still admits on its own day, then dupes as usual
#   5. undated legacy/comp codes admit on any day
#   6. single-day events mint undated codes exactly as before (regression)
#   7. comp guest with a visit date gets codes stamped to it
#   8. public order endpoint carries valid_date per ticket
#
# Run: DATABASE_URL="postgresql://test@/eventnxt_test?host=/tmp&port=5433" python3 test/test_multiday.py
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

EV = str(uuid.uuid4())        # 3-day festival
EV2 = str(uuid.uuid4())       # single-day regression event
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
    event_data = {"organization_id": ORG_ID, "name": "Runway Test", "start_date": None, "end_date": None}


app.dependency_overrides[get_current_user] = lambda: FakeUser()
app.dependency_overrides[require_event_access] = lambda event_id: FakeUser()
client = TestClient(app)
H = {"Authorization": "Bearer tok"}


def bootstrap(ev, name, qty, admits=1):
    tt = client.post(
        f"/events/{ev}/ticket-types",
        json={"name": name, "description": None, "price_cents": 0, "quantity": qty,
              "max_per_order": qty, "admits": admits, "seating_category_id": None,
              "sales_start": None, "sales_end": None, "is_active": True, "sort_order": 0},
        headers=H,
    ).json()
    client.patch(f"/events/{ev}/profile/refund-policy", json={"refund_policy": "none"}, headers=H)
    slug = client.post(f"/events/{ev}/profile/publish", headers=H).json()["slug"]
    return tt, slug


def buy(slug, tt_id, qty=1):
    return client.post(
        f"/public/events/{slug}/checkout",
        json={"buyer_name": "B", "buyer_email": "b@x.com",
              "items": [{"ticket_type_id": tt_id, "quantity": qty}]},
    )


def main():
    # ---- 1. settings guards ----
    r = client.patch(f"/events/{EV}/settings", json={"ticket_span": "multi_day"}, headers=H)
    check("span without days refused", r.status_code == 400, r.text)
    r = client.patch(f"/events/{EV}/settings",
                     json={"ticket_span": "multi_day", "first_day": "not-a-date", "last_day": D3}, headers=H)
    check("garbage date refused", r.status_code == 400, r.text)
    r = client.patch(f"/events/{EV}/settings",
                     json={"ticket_span": "multi_day", "first_day": D3, "last_day": D1}, headers=H)
    check("reversed range refused", r.status_code == 400, r.text)
    r = client.patch(f"/events/{EV}/settings",
                     json={"ticket_span": "multi_day", "first_day": D1, "last_day": D3}, headers=H)
    check("span + days accepted and echoed",
          r.status_code == 200 and r.json()["ticket_span"] == "multi_day" and r.json()["first_day"] == D1, r.text)

    # ---- 2. whole-event purchase mints one dated code per day ----
    tt, slug = bootstrap(EV, "Weekend Pass", 50)
    r = buy(slug, tt["id"])
    check("pass checkout ok", r.status_code == 200, r.text)
    token = r.json()["order_token"]
    order = client.get(f"/public/orders/{token}").json()
    dates = sorted(t["valid_date"] for t in order["tickets"])
    check("3 dated codes, one per day", dates == [D1, D2, D3], dates)

    # ---- 3. admits fans out per day ----
    tt2, _ = bootstrap(EV, "Table for 2", 10, admits=2)
    r = buy(slug, tt2["id"])
    order2 = client.get(f"/public/orders/{r.json()['order_token']}").json()
    d_counts = {}
    for t in order2["tickets"]:
        d_counts[t["valid_date"]] = d_counts.get(t["valid_date"], 0) + 1
    check("admits 2 -> 2 codes per day (6 total)", d_counts == {D1: 2, D2: 2, D3: 2}, d_counts)

    # ---- 4. door verdicts ----
    day1_code = next(t["code"] for t in order["tickets"] if t["valid_date"] == D1)
    scan = client.post(f"/events/{EV}/check-in/{day1_code}?day={D2}", headers=H).json()
    check("wrong day rejected with the code's date",
          scan["result"] == "wrong_day" and scan["valid_date"] == D1, scan)
    scan = client.post(f"/events/{EV}/check-in/{day1_code}?day={D1}", headers=H).json()
    check("right day admits (wrong-day scan didn't consume)", scan["result"] == "admitted", scan)
    scan = client.post(f"/events/{EV}/check-in/{day1_code}?day={D1}", headers=H).json()
    check("second scan same day is a dupe", scan["result"] == "already_checked_in", scan)

    # ---- 5 + 7. comps: visit-dated stamped, undated admits any day ----
    gt = client.post(f"/events/{EV}/guest-types", json={"name": "Press", "guest_mode": "invite"}, headers=H).json()
    g1 = client.post(f"/events/{EV}/guests",
                     json={"name": "Day Guest", "email": "d@x.com", "guest_type_id": gt["id"],
                           "allocation_status": "confirmed", "party_size": 1, "visit_date": D2},
                     headers=H).json()
    client.post(f"/public/rsvp/{g1['rsvp_token']}/respond", json={"attending": True})
    codes = client.get(f"/public/rsvp/{g1['rsvp_token']}").json().get("ticket_codes") or []
    scan = client.post(f"/events/{EV}/check-in/{codes[0]}?day={D1}", headers=H).json()
    check("visit-dated comp rejected on other days", scan["result"] == "wrong_day" and scan["valid_date"] == D2, scan)
    g2 = client.post(f"/events/{EV}/guests",
                     json={"name": "Any Guest", "email": "a@x.com", "guest_type_id": gt["id"],
                           "allocation_status": "confirmed", "party_size": 1},
                     headers=H).json()
    client.post(f"/public/rsvp/{g2['rsvp_token']}/respond", json={"attending": True})
    codes2 = client.get(f"/public/rsvp/{g2['rsvp_token']}").json().get("ticket_codes") or []
    # Slice 4: a whole-event comp at a multi-day event fans out to one
    # dated code per day (legacy undated codes still admit any day —
    # covered in test_compday.py).
    check("whole-event comp mints one dated code per day", len(codes2) == 3, codes2)
    scan = client.post(f"/events/{EV}/check-in/{codes2[-1]}?day={D3}", headers=H).json()
    ok_d3 = scan["result"] == "admitted" or (scan["result"] == "wrong_day" and scan["valid_date"] in (D1, D2))
    if scan["result"] != "admitted":
        # order isn't guaranteed — find the D3 code and admit it
        for c in codes2:
            scan = client.post(f"/events/{EV}/check-in/{c}?day={D3}", headers=H).json()
            if scan["result"] == "admitted":
                break
    check("the D3-dated comp code admits on D3", scan["result"] == "admitted", scan)

    # ---- 6. single-day regression ----
    tt3, slug2 = bootstrap(EV2, "GA", 20)
    r = buy(slug2, tt3["id"], qty=2)
    order3 = client.get(f"/public/orders/{r.json()['order_token']}").json()
    check("single-day event mints undated codes",
          len(order3["tickets"]) == 2 and all(t["valid_date"] is None for t in order3["tickets"]), order3["tickets"])
    scan = client.post(f"/events/{EV2}/check-in/{order3['tickets'][0]['code']}", headers=H).json()
    check("undated code admits with no day param", scan["result"] == "admitted", scan)

    # ---- 9. Events360 dates are authoritative ----
    EV3, EV4 = str(uuid.uuid4()), str(uuid.uuid4())
    FakeUser.event_data = {"organization_id": ORG_ID, "name": "Fest", "start_date": "2026-10-07", "end_date": "2026-10-09"}
    r = client.patch(f"/events/{EV3}/settings", json={"ticket_span": "per_day"}, headers=H)
    check("multi-day event: span alone auto-fills days from Events360",
          r.status_code == 200 and r.json()["first_day"] == "2026-10-07" and r.json()["last_day"] == "2026-10-09", r.text)
    FakeUser.event_data = {"organization_id": ORG_ID, "name": "Fest", "start_date": "2026-10-07", "end_date": "2026-10-10"}
    r = client.get(f"/events/{EV3}/settings", headers=H)
    check("date change in Events360 self-heals on read", r.json()["last_day"] == "2026-10-10", r.json())
    FakeUser.event_data = {"organization_id": ORG_ID, "name": "One Night", "start_date": "2026-10-08", "end_date": "2026-10-08"}
    r = client.patch(f"/events/{EV4}/settings", json={"ticket_span": "multi_day"}, headers=H)
    check("one-day event refuses multi-day spans", r.status_code == 400 and "one-day" in r.text, r.text)
    r = client.get(f"/events/{EV4}/settings", headers=H)
    check("one-day event reads as single_day", r.json()["ticket_span"] == "single_day" and r.json()["first_day"] is None, r.json())
    FakeUser.event_data = {"organization_id": ORG_ID, "name": "Runway Test", "start_date": None, "end_date": None}

    print()
    if failures:
        print("FAILURES:", failures)
        raise SystemExit(1)
    print("multiday slice 1: all clear")


if __name__ == "__main__":
    main()