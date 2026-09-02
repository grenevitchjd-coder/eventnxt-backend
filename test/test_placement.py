# eventnxt-backend: test/test_placement.py  (verification harness)
#
# Placement policies (Invites slice C2) against a REAL Postgres:
#   1. spread: sequential confirms round-robin to the emptiest allowed
#      section (C, then D, then E)
#   2. a party never splits — a party of 2 lands whole in one section
#   3. together: fills the first declared section until full, then the next
#   4. excluded sections are never chosen even when they're the only room
#   5. cohort: two recipients of the same allocation, same day, land in
#      the SAME section; with cohort_together off they spread
#   6. priority create validates allowed sections exist in the pool
#
# Run: DATABASE_URL="postgresql://test@/eventnxt_test?host=/tmp&port=5433" python3 test/test_placement.py
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
D1, D3 = "2026-10-09", "2026-10-11"
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


def make_row_pool(name, sections):
    pool = client.post(f"/events/{EV}/seating-categories",
                       json={"name": name, "capacity": 1, "sales_grain": "row", "row_label": name},
                       headers=H).json()
    client.put(f"/events/{EV}/seating-categories/{pool['id']}/sections",
               json={"sections": [{"section_label": s, "row_label": name, "capacity": c} for s, c in sections]},
               headers=H)
    return pool


def confirmed_guest(name, gt_id, party=1, extra=None):
    body = {"name": name, "email": f"{name.lower().replace(' ', '')}@x.com", "guest_type_id": gt_id,
            "allocation_status": "confirmed", "party_size": party, "guest_mode": "invite"}
    if extra:
        body.update(extra)
    return client.post(f"/events/{EV}/guests", json=body, headers=H)


def main():
    client.patch(f"/events/{EV}/settings", json={"ticket_span": "mixed"}, headers=H)

    # ---- 1 + 2: spread ----
    pool = make_row_pool("Row 4", [("C", 4), ("D", 4), ("E", 4)])
    gt = client.post(f"/events/{EV}/guest-types", json={"name": "Model", "guest_mode": "invite"}, headers=H).json()
    r = client.post(f"/events/{EV}/guest-types/{gt['id']}/seating-priorities",
                    json={"seating_category_id": pool["id"], "allowed_sections": ["C", "D", "E"], "placement": "spread"},
                    headers=H)
    check("spread priority saved", r.status_code == 201 and r.json()["placement"] == "spread", r.text)
    g1 = confirmed_guest("Spread One", gt["id"]).json()
    g2 = confirmed_guest("Spread Two", gt["id"]).json()
    g3 = confirmed_guest("Spread Three", gt["id"]).json()
    labels = {g1["section_label"], g2["section_label"], g3["section_label"]}
    check("three guests round-robin across three sections", labels == {"C", "D", "E"},
          [g1["section_label"], g2["section_label"], g3["section_label"]])
    g4 = confirmed_guest("Pair", gt["id"], party=2).json()
    check("a party of 2 lands whole in one section", g4["section_label"] in {"C", "D", "E"}, g4["section_label"])

    # ---- 3: together ----
    pool2 = make_row_pool("Row 5", [("F", 2), ("G", 4)])
    gt2 = client.post(f"/events/{EV}/guest-types", json={"name": "Press", "guest_mode": "invite"}, headers=H).json()
    client.post(f"/events/{EV}/guest-types/{gt2['id']}/seating-priorities",
                json={"seating_category_id": pool2["id"], "allowed_sections": ["F", "G"], "placement": "together"},
                headers=H)
    a = confirmed_guest("T One", gt2["id"]).json()
    b = confirmed_guest("T Two", gt2["id"]).json()
    c = confirmed_guest("T Three", gt2["id"]).json()
    check("together fills F (2) then spills to G",
          a["section_label"] == "F" and b["section_label"] == "F" and c["section_label"] == "G",
          [a["section_label"], b["section_label"], c["section_label"]])

    # ---- 4: excluded sections never chosen ----
    pool3 = make_row_pool("Row 6", [("A", 10), ("H", 1)])
    gt3 = client.post(f"/events/{EV}/guest-types", json={"name": "Vol", "guest_mode": "invite"}, headers=H).json()
    client.post(f"/events/{EV}/guest-types/{gt3['id']}/seating-priorities",
                json={"seating_category_id": pool3["id"], "allowed_sections": ["H"], "placement": "together"},
                headers=H)
    confirmed_guest("H One", gt3["id"])
    r = confirmed_guest("H Two", gt3["id"])
    check("excluded section (A, 10 free) never used — placement fails instead",
          r.status_code in (400, 409) or (r.status_code == 201 and r.json()["section_label"] is None), r.text)

    # ---- 5: cohorts ----
    pool4 = make_row_pool("Row 7", [("J", 6), ("K", 6)])
    gt4 = client.post(f"/events/{EV}/guest-types", json={"name": "Sponsor", "guest_mode": "distribute"}, headers=H).json()
    client.post(f"/events/{EV}/guest-types/{gt4['id']}/seating-priorities",
                json={"seating_category_id": pool4["id"], "allowed_sections": ["J", "K"], "placement": "spread"},
                headers=H)
    parent = client.post(f"/events/{EV}/guests",
                         json={"name": "Model P", "email": "mp@x.com", "guest_type_id": gt4["id"],
                               "allocation_status": "confirmed", "party_size": 1, "guest_mode": "distribute",
                               "cohort_together": True,
                               "ticket_allotment": [{"date": D1, "quantity": 4}]},
                         headers=H).json()
    r = client.post(f"/public/rsvp/{parent['rsvp_token']}/distribute",
                    json={"recipients": [
                        {"name": "Friend A", "email": "fa@x.com", "visit_date": D1, "party_size": 1},
                        {"name": "Friend B", "email": "fb@x.com", "visit_date": D1, "party_size": 1},
                    ]})
    check("distribution accepted", r.status_code == 200, r.text)
    kids = [g for g in client.get(f"/events/{EV}/guests", headers=H).json()
            if g["name"] in ("Friend A", "Friend B")]
    for k in kids:
        client.post(f"/public/rsvp/{k['rsvp_token']}/respond", json={"attending": True})
    kids = [g for g in client.get(f"/events/{EV}/guests", headers=H).json()
            if g["name"] in ("Friend A", "Friend B")]
    labels = {k["section_label"] for k in kids}
    check("same-allocation same-day recipients sit together", len(labels) == 1 and labels != {None}, labels)

    parent2 = client.post(f"/events/{EV}/guests",
                          json={"name": "Sponsor Q", "email": "sq@x.com", "guest_type_id": gt4["id"],
                                "allocation_status": "confirmed", "party_size": 1, "guest_mode": "distribute",
                                "cohort_together": False,
                                "ticket_allotment": [{"date": D1, "quantity": 4}]},
                          headers=H).json()
    client.post(f"/public/rsvp/{parent2['rsvp_token']}/distribute",
                json={"recipients": [
                    {"name": "Client X", "email": "cx@x.com", "visit_date": D1, "party_size": 1},
                    {"name": "Client Y", "email": "cy@x.com", "visit_date": D1, "party_size": 1},
                ]})
    for k in [g for g in client.get(f"/events/{EV}/guests", headers=H).json() if g["name"] in ("Client X", "Client Y")]:
        client.post(f"/public/rsvp/{k['rsvp_token']}/respond", json={"attending": True})
    kids2 = [g for g in client.get(f"/events/{EV}/guests", headers=H).json() if g["name"] in ("Client X", "Client Y")]
    labels2 = {k["section_label"] for k in kids2}
    check("cohort off: recipients spread individually", len(labels2) == 2, labels2)

    # ---- 6: validation ----
    r = client.post(f"/events/{EV}/guest-types/{gt['id']}/seating-priorities",
                    json={"seating_category_id": pool["id"], "allowed_sections": ["Z"], "placement": "spread"},
                    headers=H)
    check("nonexistent allowed section refused", r.status_code == 400, r.text)

    print()
    if failures:
        print("FAILURES:", failures)
        raise SystemExit(1)
    print("placement policies: all clear")


if __name__ == "__main__":
    main()