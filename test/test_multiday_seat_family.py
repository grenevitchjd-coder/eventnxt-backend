# eventnxt-backend: test/test_multiday_seat_family.py  (verification harness)
#
# Multi-day comp auto-pick, seat-grain pools (2026-09 fix):
#   Found live: a comp guest with dated tickets across several nights
#   got a REAL seat on night one (whichever pool the priority walk
#   resolved to) but only a bare section name on every other night —
#   restamp_guest_tickets stamps a seat onto exactly the ONE night
#   whose pool it lives in, and the original auto-pick only ever
#   claimed a seat in ONE pool.
#   1. an invite guest with a 2-night ticket_allotment in a seat-grain
#      family gets the SAME seat identity (section/row/number) on BOTH
#      nights' tickets, not just the first
#   2. a second guest added the same way gets a DIFFERENT identity,
#      claimed correctly on both nights too (no double-booking a chair
#      that's only free on one of the two nights)
#
# Run: DATABASE_URL="postgresql://test@/eventnxt_test?host=/tmp&port=5433" python3 test/test_multiday_seat_family.py
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

email_mod.send_email = lambda *a, **k: True

EV, ORG = str(uuid.uuid4()), str(uuid.uuid4())
D1, D2 = "2026-12-24", "2026-12-25"
failures = []


def check(name, cond, extra=""):
    print(("  ok " if cond else "  ✗ ") + name + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


class FakeUser:
    user_id = "u-1"
    organization_id = ORG
    name = "Owner"
    email = "o@example.com"
    role = "owner"
    raw_token = "tok"
    event_data = {"organization_id": ORG, "name": "Multiday Seat Test", "start_date": D1, "end_date": D2}


app.dependency_overrides[get_current_user] = lambda: FakeUser()
app.dependency_overrides[require_event_access] = lambda event_id: FakeUser()
client = TestClient(app)
H = {"Authorization": "Bearer tok"}


def main():
    client.patch(f"/events/{EV}/settings", json={"ticket_span": "per_day", "ticketing_mode": "native"}, headers=H)

    # A seat-grain pool with 4 seats in one section, fanned out to both nights.
    pool = client.post(f"/events/{EV}/seating-categories",
                        json={"name": "Row 1", "capacity": 4, "sales_grain": "seat", "row_label": "Row 1"},
                        headers=H).json()
    client.put(f"/events/{EV}/seating-categories/{pool['id']}/sections",
               json={"sections": [{"section_label": "A", "row_label": "Row 1", "capacity": 4}]}, headers=H)
    tt = client.post(f"/events/{EV}/ticket-types",
                      json={"name": "Row 1", "description": None, "price_cents": 0, "quantity": 4,
                            "max_per_order": 4, "admits": 1, "seating_category_id": pool["id"], "valid_date": D1,
                            "sales_start": None, "sales_end": None, "is_active": True, "sort_order": 0},
                      headers=H).json()
    client.post(f"/events/{EV}/ticket-types/{tt['id']}/fan-out", json={}, headers=H)

    gt = client.post(f"/events/{EV}/guest-types", json={"name": "VIP", "guest_mode": "invite"}, headers=H).json()
    client.post(f"/events/{EV}/guest-types/{gt['id']}/seating-priorities",
                json={"seating_category_id": pool["id"], "section_label": "A"}, headers=H)

    def add_guest(name):
        return client.post(
            f"/events/{EV}/guests",
            json={
                "name": name, "email": f"{name.lower().replace(' ', '')}@x.com", "guest_type_id": gt["id"],
                "allocation_status": "confirmed", "party_size": 1, "guest_mode": "invite",
                "ticket_allotment": [{"date": D1, "quantity": 1}, {"date": D2, "quantity": 1}],
            },
            headers=H,
        ).json()

    def seat_labels_by_day(guest_id):
        codes = client.get(f"/events/{EV}/guests/roster/door", headers=H).json()
        row = next(g for g in codes if g["id"] == guest_id)
        return {t["valid_date"]: t.get("seat_label") for t in row["tickets"]}

    print("1) one guest, two nights — same seat identity both nights")
    g1 = add_guest("Jacob Test")
    labels1 = seat_labels_by_day(g1["id"])
    check("both nights minted", set(labels1.keys()) == {D1, D2}, labels1)
    check("night one has a REAL seat (not just a section)",
          labels1.get(D1) and "Seat" in labels1[D1], labels1.get(D1))
    check("night two ALSO has a real seat, not a bare section fallback",
          labels1.get(D2) and "Seat" in labels1[D2], labels1.get(D2))
    check("the SAME physical seat both nights",
          labels1.get(D1) == labels1.get(D2), labels1)

    print("2) a second guest gets a DIFFERENT seat, correctly on both nights")
    g2 = add_guest("Second Guest")
    labels2 = seat_labels_by_day(g2["id"])
    check("second guest also gets a real, matching seat both nights",
          labels2.get(D1) and labels2.get(D1) == labels2.get(D2), labels2)
    check("second guest's seat differs from the first guest's",
          labels2.get(D1) != labels1.get(D1), (labels1, labels2))

    print()
    if failures:
        print(f"FAILED: {len(failures)} — " + ", ".join(failures))
        sys.exit(1)
    print("test_multiday_seat_family: all checks passed")


main()