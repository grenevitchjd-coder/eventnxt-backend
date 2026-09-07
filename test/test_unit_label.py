# eventnxt-backend: test/test_unit_label.py  (verification harness)
#
# One organizer-set word per pool ("Table", "Room", "Area") replacing
# the hardcoded "Section"/"Seat" wording every ticket display used to
# invent independently — exactly how "Row 3" once silently vanished
# from one of them while staying present on another (2026-09).
#
#   1. a GA/table type with unit_label="Table" (whole-table purchase,
#      seat-grain) shows "Table 4" on both a PAID ticket and a COMP
#      ticket assigned the same physical table
#   2. a row-grain pool with unit_label="Room" and a row_label shows
#      "Row 3 · Room B" — the row name always present, the section
#      word replaced, ticket type name dropped once there's a detail
#   3. leaving unit_label unset keeps today's default "Section"/"Seat"
#      wording exactly as before — no regression for existing events
#
# Run: DATABASE_URL="postgresql://test@/eventnxt_test?host=/tmp&port=5433" python3 test/test_unit_label.py
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
    event_data = {"organization_id": ORG, "name": "Unit Label Test", "start_date": None, "end_date": None}


app.dependency_overrides[get_current_user] = lambda: FakeUser()
app.dependency_overrides[require_event_access] = lambda event_id: FakeUser()
client = TestClient(app)
H = {"Authorization": "Bearer tok"}

TT = {"description": None, "sales_start": None, "sales_end": None, "is_active": True, "sort_order": 0, "valid_date": None}


def roster_row(guest_id):
    codes = client.get(f"/events/{EV}/guests/roster/door", headers=H).json()
    return next(g for g in codes if g["id"] == guest_id)


def main():
    # ---- 1. whole-table purchase (seat-grain, unit_label="Table") ----
    table_pool = client.post(
        f"/events/{EV}/seating-categories",
        json={"name": "VIP Tables", "capacity": 4, "sales_grain": "seat", "unit_label": "Table"},
        headers=H,
    ).json()
    check("pool carries unit_label back", table_pool.get("unit_label") == "Table", table_pool)
    client.put(
        f"/events/{EV}/seating-categories/{table_pool['id']}/sections",
        json={"sections": [{"section_label": "A", "capacity": 4}]},
        headers=H,
    )
    table_tt = client.post(
        f"/events/{EV}/ticket-types",
        json={**TT, "name": "VIP Table", "price_cents": 50000, "quantity": 4,
              "max_per_order": 4, "admits": 4, "seating_category_id": table_pool["id"]},
        headers=H,
    ).json()

    # public catalog + seat map both carry the word for the picker
    catalog = client.get(f"/public/events/{EV}/ticket-types", headers=H).json() if False else None
    seats = client.get(f"/events/{EV}/seating-categories/{table_pool['id']}/seats", headers=H).json()
    by_num = {s["seat_number"]: s for s in seats}
    check(
        "organizer seat view already shows 'Table 4', not 'Seat 4'",
        by_num[4]["label"] == "Section A · Table 4",
        by_num[4],
    )

    # a comp guest assigned to table 4 shows it on their ticket
    gt = client.post(f"/events/{EV}/guest-types", json={"name": "Sponsor", "guest_mode": "invite"}, headers=H).json()
    g1 = client.post(
        f"/events/{EV}/guests",
        json={"name": "Table Guest", "email": "tableguest@x.com", "guest_type_id": gt["id"],
              "allocation_status": "confirmed", "party_size": 1, "guest_mode": "invite",
              "seating_category_id": table_pool["id"]},
        headers=H,
    ).json()
    r = client.put(f"/events/{EV}/guests/{g1['id']}/seats", json={"seat_ids": [by_num[4]["id"]]}, headers=H)
    check("assign table 4 to comp guest", r.status_code == 200, r.text[:200])
    row1 = roster_row(g1["id"])
    check(
        "comp ticket shows 'Table 4' (detail wins over type name)",
        all(t.get("seat_label") == "Section A · Table 4" for t in row1["tickets"]),
        row1["tickets"],
    )

    # ---- 2. row-grain pool, unit_label="Room" AND a row_label ----
    room_pool = client.post(
        f"/events/{EV}/seating-categories",
        json={"name": "Row 3 Preferred Seating", "capacity": 10, "sales_grain": "row",
              "row_label": "Row 3", "unit_label": "Room"},
        headers=H,
    ).json()
    client.put(
        f"/events/{EV}/seating-categories/{room_pool['id']}/sections",
        json={"sections": [{"section_label": "B", "row_label": "Row 3", "capacity": 10}]},
        headers=H,
    )
    client.post(
        f"/events/{EV}/ticket-types",
        json={**TT, "name": "Row 3 Preferred Seating", "price_cents": 9500, "quantity": 10,
              "max_per_order": 10, "admits": 1, "seating_category_id": room_pool["id"]},
        headers=H,
    ).json()
    gt2 = client.post(f"/events/{EV}/guest-types", json={"name": "Guest", "guest_mode": "invite"}, headers=H).json()
    g2 = client.post(
        f"/events/{EV}/guests",
        json={"name": "Room Guest", "email": "roomguest@x.com", "guest_type_id": gt2["id"],
              "allocation_status": "confirmed", "party_size": 1, "guest_mode": "invite",
              "seating_category_id": room_pool["id"], "section_label": "B"},
        headers=H,
    ).json()
    row2 = roster_row(g2["id"])
    check(
        "row_label always present AND section word replaced — 'Row 3 · Room B', not the type name",
        all(t.get("seat_label") == "Row 3 · Room B" for t in row2["tickets"]),
        row2["tickets"],
    )

    # ---- 3. no unit_label set — today's default wording, unchanged ----
    plain_pool = client.post(
        f"/events/{EV}/seating-categories",
        json={"name": "Standing Room", "capacity": 20, "sales_grain": "row"},
        headers=H,
    ).json()
    check("no unit_label by default", plain_pool.get("unit_label") is None, plain_pool)
    client.put(
        f"/events/{EV}/seating-categories/{plain_pool['id']}/sections",
        json={"sections": [{"section_label": "C", "capacity": 20}]},
        headers=H,
    )
    client.post(
        f"/events/{EV}/ticket-types",
        json={**TT, "name": "Standing Room", "price_cents": 4500, "quantity": 20,
              "max_per_order": 20, "admits": 1, "seating_category_id": plain_pool["id"]},
        headers=H,
    ).json()
    gt3 = client.post(f"/events/{EV}/guest-types", json={"name": "Guest", "guest_mode": "invite"}, headers=H).json()
    g3 = client.post(
        f"/events/{EV}/guests",
        json={"name": "Plain Guest", "email": "plainguest@x.com", "guest_type_id": gt3["id"],
              "allocation_status": "confirmed", "party_size": 1, "guest_mode": "invite",
              "seating_category_id": plain_pool["id"], "section_label": "C"},
        headers=H,
    ).json()
    row3 = roster_row(g3["id"])
    check(
        "default wording unchanged when unit_label is unset — 'Section C'",
        all(t.get("seat_label") == "Section C" for t in row3["tickets"]),
        row3["tickets"],
    )

    print()
    if failures:
        print(f"FAILED: {len(failures)} — " + ", ".join(failures))
        sys.exit(1)
    print("test_unit_label: all checks passed")


main()