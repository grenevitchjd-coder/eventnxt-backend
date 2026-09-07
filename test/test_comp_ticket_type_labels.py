# eventnxt-backend: test/test_comp_ticket_type_labels.py  (verification harness)
#
# Comp tickets never carried a ticket_type_id at all before 2026-09 —
# only paid orders did. So once there was no specific seat to fall back
# to, an unassigned-section comp or a GA/table type ("Champagne Lounge",
# admits 4) showed nothing identifying on the ticket at all — just
# "General admission", the same for every comp regardless of what they
# were actually invited to.
#
#   1. a comp confirmed into a GA/table type (no sections, admits 4)
#      shows that type's NAME on their ticket ("Champagne Lounge")
#   2. a comp confirmed into a ROW-grain (unassigned sectioned) type
#      with NO section chosen ("Anywhere") shows the type's name alone
#      instead of "General admission"
#   3. an EXISTING ticket minted before this fix (ticket_type_id back-
#      filled to None) gets it filled in when tickets are re-synced
#      ("Update & resend"), not just on brand-new mints
#
# Run: DATABASE_URL="postgresql://test@/eventnxt_test?host=/tmp&port=5433" python3 test/test_comp_ticket_type_labels.py
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uuid

os.environ.setdefault("DATABASE_URL", "postgresql://test@/eventnxt_test?host=/tmp&port=5433")

from fastapi.testclient import TestClient

import app.services.email as email_mod
from app.database import SessionLocal
from app.main import app
from app.models.ticket import Ticket
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
    event_data = {"organization_id": ORG, "name": "Ticket Label Test", "start_date": None, "end_date": None}


app.dependency_overrides[get_current_user] = lambda: FakeUser()
app.dependency_overrides[require_event_access] = lambda event_id: FakeUser()
client = TestClient(app)
H = {"Authorization": "Bearer tok"}

TT = {"description": None, "sales_start": None, "sales_end": None, "is_active": True, "sort_order": 0, "valid_date": None}


def roster_row(guest_id):
    codes = client.get(f"/events/{EV}/guests/roster/door", headers=H).json()
    return next(g for g in codes if g["id"] == guest_id)


def main():
    # ---- 1. GA/table type (Champagne Lounge equivalent), admits 4, no sections ----
    lounge_pool = client.post(
        f"/events/{EV}/seating-categories",
        json={"name": "Lounge", "capacity": 3, "sales_grain": "ga"},
        headers=H,
    ).json()
    client.post(
        f"/events/{EV}/ticket-types",
        json={**TT, "name": "Champagne Lounge", "price_cents": 50000, "quantity": 3,
              "max_per_order": 3, "admits": 4, "seating_category_id": lounge_pool["id"]},
        headers=H,
    ).json()
    gt_lounge = client.post(f"/events/{EV}/guest-types", json={"name": "Sponsor", "guest_mode": "invite"}, headers=H).json()
    client.post(f"/events/{EV}/guest-types/{gt_lounge['id']}/seating-priorities",
                json={"seating_category_id": lounge_pool["id"]}, headers=H)
    g1 = client.post(
        f"/events/{EV}/guests",
        json={"name": "Lounge Guest", "email": "lounge@x.com", "guest_type_id": gt_lounge["id"],
              "allocation_status": "confirmed", "party_size": 1, "guest_mode": "invite"},
        headers=H,
    ).json()
    row1 = roster_row(g1["id"])
    check("lounge guest minted a ticket", len(row1["tickets"]) >= 1, row1)
    check(
        "GA/table ticket shows the TYPE NAME, not 'General admission'",
        all(t.get("seat_label") == "Champagne Lounge" for t in row1["tickets"]),
        row1["tickets"],
    )

    # ---- 2. row-grain type, unassigned ("Anywhere") — no section chosen ----
    row_pool = client.post(
        f"/events/{EV}/seating-categories",
        json={"name": "Row 3", "capacity": 10, "sales_grain": "row", "row_label": "Row 3"},
        headers=H,
    ).json()
    client.put(
        f"/events/{EV}/seating-categories/{row_pool['id']}/sections",
        json={"sections": [{"section_label": "B", "row_label": "Row 3", "capacity": 10}]},
        headers=H,
    )
    client.post(
        f"/events/{EV}/ticket-types",
        json={**TT, "name": "Row 3 Preferred Seating", "price_cents": 9500, "quantity": 10,
              "max_per_order": 10, "admits": 1, "seating_category_id": row_pool["id"]},
        headers=H,
    ).json()
    gt_row = client.post(f"/events/{EV}/guest-types", json={"name": "Guest", "guest_mode": "invite"}, headers=H).json()
    # deliberately NO seating-priorities on this type, and no section on
    # the guest — mirrors "Anywhere" with nothing auto-picked, which the
    # organizer said is fine AS LONG AS the ticket still names the area.
    g2 = client.post(
        f"/events/{EV}/guests",
        json={"name": "Row Guest", "email": "rowguest@x.com", "guest_type_id": gt_row["id"],
              "allocation_status": "confirmed", "party_size": 1, "guest_mode": "invite",
              "seating_category_id": row_pool["id"]},
        headers=H,
    ).json()
    check("row guest has no section chosen (Anywhere)", not g2.get("section_label"), g2)
    row2 = roster_row(g2["id"])
    check(
        "unassigned row-grain ticket shows the TYPE NAME alone, not 'General admission'",
        all(t.get("seat_label") == "Row 3 Preferred Seating" for t in row2["tickets"]),
        row2["tickets"],
    )

    # ---- 3. an existing (pre-fix) ticket backfills ticket_type_id on resync ----
    db = SessionLocal()
    db.query(Ticket).filter(Ticket.guest_id == g1["id"]).update({Ticket.ticket_type_id: None})
    db.commit()
    db.close()
    row1_wiped = roster_row(g1["id"])
    check(
        "wiping ticket_type_id reverts the label (sanity check on the test itself)",
        all(t.get("seat_label") != "Champagne Lounge" for t in row1_wiped["tickets"]),
        row1_wiped["tickets"],
    )
    r = client.post(f"/events/{EV}/guests/{g1['id']}/sync-tickets", json={"resend": False}, headers=H)
    check("sync-tickets (Update & resend) 200", r.status_code == 200, r.text[:200])
    row1_fixed = roster_row(g1["id"])
    check(
        "resync backfills ticket_type_id on the EXISTING ticket, not just new mints",
        all(t.get("seat_label") == "Champagne Lounge" for t in row1_fixed["tickets"]),
        row1_fixed["tickets"],
    )

    print()
    if failures:
        print(f"FAILED: {len(failures)} — " + ", ".join(failures))
        sys.exit(1)
    print("test_comp_ticket_type_labels: all checks passed")


main()