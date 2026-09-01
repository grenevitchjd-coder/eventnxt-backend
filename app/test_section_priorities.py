# eventnxt-backend: test_section_priorities.py  (verification harness — repo root)
#
# Section-level guest-type priorities (Slice C) against a REAL Postgres:
#   1. section priority wins while it has room; guest records the section
#   2. full section falls through to the pool-level entry (section None)
#   3. RSVP yes places into the section; door scan shows "Section A"
#   4. explicit placement into a full section refused with a section message
#   5. box-office heads in a section count against comp room there
#   6. unknown labels refused on both priority add and explicit placement
#   7. restructure that removes a label: resolver skips it, nothing 500s
#   8. seat-grain pools: room = free unblocked seats minus seatless comps
#
# Run: DATABASE_URL="postgresql://test@/eventnxt_test?host=/tmp&port=5433" python3 test_section_priorities.py
import os
import uuid

os.environ.setdefault("DATABASE_URL", "postgresql://test@/eventnxt_test?host=/tmp&port=5433")

from fastapi.testclient import TestClient

from app.main import app
from app.services.deps import get_current_user
from app.services.event_access import require_event_access

EV = str(uuid.uuid4())
ORG_ID = str(uuid.uuid4())
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


def add_guest(gt_id, name, party, category_id=None, section=None, status="confirmed"):
    payload = {
        "name": name, "email": f"{name.replace(' ', '').lower()}@x.com", "guest_type_id": gt_id,
        "allocation_status": status, "party_size": party,
    }
    if category_id:
        payload["seating_category_id"] = category_id
    if section:
        payload["section_label"] = section
    return client.post(f"/events/{EV}/guests", json=payload, headers=H)


def main():
    # ---- bootstrap: row-grain pool, sections A(2) B(4) C(6) ----
    p1 = client.post(
        f"/events/{EV}/seating-categories",
        json={"name": "Row 2", "capacity": 1, "sales_grain": "row", "row_label": "Row 2"},
        headers=H,
    ).json()
    client.put(
        f"/events/{EV}/seating-categories/{p1['id']}/sections",
        json={"sections": [
            {"section_label": "A", "row_label": "Row 2", "capacity": 2},
            {"section_label": "B", "row_label": "Row 2", "capacity": 4},
            {"section_label": "C", "row_label": "Row 2", "capacity": 6},
        ]},
        headers=H,
    )
    tt1 = client.post(
        f"/events/{EV}/ticket-types",
        json={"name": "Row 2", "description": None, "price_cents": 0, "quantity": 12,
              "max_per_order": 12, "admits": 1, "seating_category_id": p1["id"],
              "sales_start": None, "sales_end": None, "is_active": True, "sort_order": 0},
        headers=H,
    ).json()
    client.patch(f"/events/{EV}/profile/refund-policy", json={"refund_policy": "none"}, headers=H)
    slug = client.post(f"/events/{EV}/profile/publish", headers=H).json()["slug"]
    sections = {s["section_label"]: s for s in client.get(f"/events/{EV}/seating-categories", headers=H).json()[0]["sections"]}

    gtx = client.post(f"/events/{EV}/guest-types", json={"name": "Models", "guest_mode": "invite"}, headers=H).json()
    r = client.post(f"/events/{EV}/guest-types/{gtx['id']}/seating-priorities",
                    json={"seating_category_id": p1["id"], "section_label": "A"}, headers=H)
    check("priority with section accepted + echoed", r.status_code == 201 and r.json()["section_label"] == "A", r.text)
    client.post(f"/events/{EV}/guest-types/{gtx['id']}/seating-priorities",
                json={"seating_category_id": p1["id"]}, headers=H)

    # ---- 1. section priority wins ----
    g1 = add_guest(gtx["id"], "Mona Model", 1).json()
    check("guest 1 lands in Section A", g1["seating_category_id"] == p1["id"] and g1["section_label"] == "A", g1)

    # ---- 2. no room in A for party 2 -> pool-level fallthrough ----
    g2 = add_guest(gtx["id"], "Mia Model", 2).json()
    check("party of 2 falls through to pool level", g2["seating_category_id"] == p1["id"] and g2["section_label"] is None, g2)

    # ---- 3. RSVP yes places into the section; scan shows it ----
    g3 = add_guest(gtx["id"], "Pia Pending", 1, status="pending").json()
    r = client.post(f"/public/rsvp/{g3['rsvp_token']}/respond", json={"attending": True})
    check("rsvp yes ok", r.status_code == 200, r.text)
    gl = {g["id"]: g for g in client.get(f"/events/{EV}/guests", headers=H).json()}
    check("rsvp guest recorded in Section A", gl[g3["id"]]["section_label"] == "A", gl[g3["id"]])
    codes = client.get(f"/public/rsvp/{g3['rsvp_token']}").json().get("ticket_codes") or []
    scan = client.post(f"/events/{EV}/check-in/{codes[0]}", headers=H).json() if codes else {}
    check("door scan shows Section A", scan.get("seat_label") == "Section A", scan)

    # ---- 4. explicit placement into full section refused with section message ----
    r = add_guest(gtx["id"], "Eve Extra", 1, category_id=p1["id"], section="A")
    check("explicit into full Section A refused", r.status_code == 400 and "Section A" in r.text, r.text)

    # ---- 5. box office in a section counts against comp room ----
    e2 = add_guest(gtx["id"], "Bo B", 1, category_id=p1["id"], section="B").json()
    check("explicit Section B ok", e2.get("section_label") == "B", e2)
    r = client.post(
        f"/public/events/{slug}/checkout",
        json={"buyer_name": "Buyer", "buyer_email": "b@x.com",
              "items": [{"ticket_type_id": tt1["id"], "quantity": 1, "zone_section_id": sections["B"]["id"]}]},
    )
    check("box office buys 1 head in B", r.status_code == 200, r.text)
    r = add_guest(gtx["id"], "Two B", 2, category_id=p1["id"], section="B")
    check("B still fits party of 2 (4-1-1=2)", r.status_code == 201, r.text)
    r = add_guest(gtx["id"], "None B", 2, category_id=p1["id"], section="B")
    check("B refuses next party of 2 (room 0)", r.status_code == 400 and "Section B" in r.text, r.text)

    # ---- 6. unknown labels refused everywhere ----
    r = add_guest(gtx["id"], "Zed Z", 1, category_id=p1["id"], section="Z")
    check("explicit unknown section refused", r.status_code == 400 and 'no section' in r.text, r.text)
    r = client.post(f"/events/{EV}/guest-types/{gtx['id']}/seating-priorities",
                    json={"seating_category_id": p1["id"], "section_label": "Z"}, headers=H)
    check("priority with unknown section refused", r.status_code == 400, r.text)

    # ---- 7. restructure removes label A -> resolver skips, falls to pool ----
    r = client.put(
        f"/events/{EV}/seating-categories/{p1['id']}/sections",
        json={"sections": [{"section_label": "D", "row_label": "Row 2", "capacity": 12}]},
        headers=H,
    )
    check("rename sections to D ok", r.status_code == 200, r.text)
    g4 = add_guest(gtx["id"], "Post Restructure", 1).json()
    check("stale 'A' priority skipped, pool-level wins", g4.get("section_label") is None and g4.get("seating_category_id") == p1["id"], g4)

    # ---- 8. seat-grain room: free unblocked seats minus seatless comps ----
    p2 = client.post(
        f"/events/{EV}/seating-categories",
        json={"name": "Front", "capacity": 1, "sales_grain": "seat"},
        headers=H,
    ).json()
    client.put(
        f"/events/{EV}/seating-categories/{p2['id']}/sections",
        json={"sections": [{"section_label": "S", "row_label": None, "capacity": 3}]},
        headers=H,
    )
    seats = client.get(f"/events/{EV}/seating-categories/{p2['id']}/seats", headers=H).json()
    client.post(f"/events/{EV}/seating-categories/{p2['id']}/seats/block",
                json={"seat_ids": [seats[0]["id"]], "label": "Broken"}, headers=H)
    gty = client.post(f"/events/{EV}/guest-types", json={"name": "VIP", "guest_mode": "invite"}, headers=H).json()
    client.post(f"/events/{EV}/guest-types/{gty['id']}/seating-priorities",
                json={"seating_category_id": p2["id"], "section_label": "S"}, headers=H)
    gy1 = add_guest(gty["id"], "Vic Vip", 2).json()
    check("seat pool: party 2 fits (3 seats - 1 blocked)", gy1.get("section_label") == "S", gy1)
    r = add_guest(gty["id"], "No Room", 1)
    check("seat pool: next guest refused (free 2 - seatless comps 2 = 0)",
          r.status_code == 400 and "full" in r.text, r.text)

    print()
    if failures:
        print("FAILURES:", failures)
        raise SystemExit(1)
    print("section priorities: all clear")


if __name__ == "__main__":
    main()