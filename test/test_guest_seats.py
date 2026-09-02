# eventnxt-backend: test/test_guest_seats.py  (verification harness)
#
# Guest seat assignment (Slice B) against a REAL Postgres:
#   1. assign reserved seats 3,4 to a press guest -> seats carry the guest,
#      stay reserved; guest gains seat_labels
#   2. RSVP yes -> comp tickets mint WITH seats; priority resolver does not
#      move a hand-placed guest; door scan shows the seat label
#   3. buyer can't buy an assigned seat
#   4. sold seat / another guest's seat can't be assigned
#   5. wholesale reassign [4,6]: 3 releases (stays reserved), 6 auto-reserves,
#      tickets re-stamp to exactly {4,6}
#   6. RSVP no -> seats release from guest but stay reserved
#   7. assign-then-mint and mint-then-assign converge (guest 2)
#
# Run: DATABASE_URL="postgresql://test@/eventnxt_test?host=/tmp&port=5433" python3 test/test_guest_seats.py
import os
import sys
from pathlib import Path

# Runs from the repo root (python3 test/<file>.py) or from inside
# test/ — either way, make the repo root importable for `app`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uuid

os.environ.setdefault("DATABASE_URL", "postgresql://test@/eventnxt_test?host=/tmp&port=5433")

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models.seat import Seat
from app.models.ticket import Ticket, TicketStatus
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


def db_ticket_seats(guest_id):
    """Set of seat_numbers on the guest's VALID tickets (None filtered)."""
    db = SessionLocal()
    try:
        rows = (
            db.query(Seat.seat_number)
            .join(Ticket, Ticket.seat_id == Seat.id)
            .filter(Ticket.guest_id == guest_id, Ticket.status == TicketStatus.VALID)
            .all()
        )
        n_tickets = (
            db.query(Ticket).filter(Ticket.guest_id == guest_id, Ticket.status == TicketStatus.VALID).count()
        )
        return {n for (n,) in rows}, n_tickets
    finally:
        db.close()


def main():
    # ---- bootstrap: assigned pool (6 seats), native ticketing, published ----
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
    seats = client.get(f"/events/{EV}/seating-categories/{pool['id']}/seats", headers=H).json()
    by_num = {s["seat_number"]: s for s in seats}
    client.post(
        f"/events/{EV}/seating-categories/{pool['id']}/seats/block",
        json={"seat_ids": [by_num[n]["id"] for n in (3, 4, 5)], "label": "Press"},
        headers=H,
    )
    gt = client.post(f"/events/{EV}/guest-types", json={"name": "Press", "guest_mode": "invite"}, headers=H).json()
    guest = client.post(
        f"/events/{EV}/guests",
        json={"name": "Pat Press", "email": "pat@press.com", "guest_type_id": gt["id"],
              "seating_category_id": pool["id"], "allocation_status": "pending", "party_size": 2},
        headers=H,
    ).json()

    # ---- 1. assign reserved 3,4 ----
    r = client.put(
        f"/events/{EV}/guests/{guest['id']}/seats",
        json={"seat_ids": [by_num[3]["id"], by_num[4]["id"]]},
        headers=H,
    )
    check("assign 3,4 to press guest", r.status_code == 200, r.text)
    body = r.json()
    vb = {s["seat_number"]: s for s in body["seats"]}
    check(
        "3,4 carry guest + stay reserved",
        all(vb[n]["guest_name"] == "Pat Press" and vb[n]["status"] == "reserved" for n in (3, 4))
        and vb[5]["guest_name"] is None,
    )
    check("guest gains seat_labels", len(body["guest"]["seat_labels"]) == 2, body["guest"])

    # ---- 2. RSVP yes: mints WITH seats, resolver doesn't move guest ----
    r = client.post(f"/public/rsvp/{guest['rsvp_token']}/respond", json={"attending": True})
    check("rsvp yes ok", r.status_code == 200, r.text)
    nums, n_tickets = db_ticket_seats(guest["id"])
    check("2 tickets minted stamped 3,4", n_tickets == 2 and nums == {3, 4}, f"{n_tickets} {nums}")
    gview = client.get(f"/events/{EV}/guests", headers=H).json()
    gme = next(x for x in gview if x["id"] == guest["id"])
    check("guest stayed in hand-placed pool", gme["seating_category_id"] == pool["id"])
    # door scan shows the seat
    db = SessionLocal()
    code = db.query(Ticket.code).filter(Ticket.guest_id == guest["id"]).order_by(Ticket.created_at).first()[0]
    db.close()
    scan = client.post(f"/events/{EV}/check-in/{code}", headers=H).json()
    check("door scan shows seat label", scan["result"] == "admitted" and "Seat" in (scan.get("seat_label") or ""), scan)

    # ---- 3. buyer can't buy an assigned seat ----
    r = client.post(
        f"/public/events/{slug}/checkout",
        json={"buyer_name": "B", "buyer_email": "b@x.com",
              "items": [{"ticket_type_id": tt["id"], "quantity": 1, "seat_ids": [by_num[4]["id"]]}]},
    )
    check("buyer refused on assigned seat", r.status_code == 400, r.text)

    # ---- 4. can't assign sold / other-guest seats ----
    r = client.post(
        f"/public/events/{slug}/checkout",
        json={"buyer_name": "B", "buyer_email": "b@x.com",
              "items": [{"ticket_type_id": tt["id"], "quantity": 1, "seat_ids": [by_num[1]["id"]]}]},
    )
    check("seat 1 sells to a buyer", r.status_code == 200, r.text)
    guest2 = client.post(
        f"/events/{EV}/guests",
        json={"name": "Vic VIP", "email": "vic@vip.com", "guest_type_id": gt["id"],
              "seating_category_id": pool["id"], "allocation_status": "pending", "party_size": 1},
        headers=H,
    ).json()
    r = client.put(f"/events/{EV}/guests/{guest2['id']}/seats", json={"seat_ids": [by_num[1]["id"]]}, headers=H)
    check("assigning sold seat refused", r.status_code == 400, r.text)
    r = client.put(f"/events/{EV}/guests/{guest2['id']}/seats", json={"seat_ids": [by_num[4]["id"]]}, headers=H)
    check("assigning another guest's seat refused", r.status_code == 400, r.text)

    # ---- 5. wholesale reassign [4,6] ----
    r = client.put(
        f"/events/{EV}/guests/{guest['id']}/seats",
        json={"seat_ids": [by_num[4]["id"], by_num[6]["id"]]},
        headers=H,
    )
    check("reassign to 4,6 ok", r.status_code == 200, r.text)
    vb = {s["seat_number"]: s for s in r.json()["seats"]}
    check(
        "3 released-but-reserved, 6 auto-reserved+assigned",
        vb[3]["guest_name"] is None and vb[3]["status"] == "reserved"
        and vb[6]["guest_name"] == "Pat Press" and vb[6]["status"] == "reserved"
        and vb[6]["block_label"] == "Pat Press",
        vb,
    )
    nums, n_tickets = db_ticket_seats(guest["id"])
    check("tickets re-stamped to exactly 4,6", n_tickets == 2 and nums == {4, 6}, f"{n_tickets} {nums}")

    # ---- 6. RSVP no releases assignment, keeps reservations ----
    r = client.post(f"/public/rsvp/{guest['rsvp_token']}/respond", json={"attending": False})
    check("rsvp no ok", r.status_code == 200, r.text)
    view = client.get(f"/events/{EV}/seating-categories/{pool['id']}/seats", headers=H).json()
    vb = {s["seat_number"]: s for s in view}
    check(
        "decline: 4,6 unassigned but still reserved",
        all(vb[n]["guest_name"] is None and vb[n]["status"] == "reserved" for n in (4, 6)),
        vb,
    )
    nums, _ = db_ticket_seats(guest["id"])
    check("decline clears ticket seat stamps", nums == set(), nums)

    # ---- 7. assign-then-mint (guest2 has no tickets yet) ----
    r = client.put(f"/events/{EV}/guests/{guest2['id']}/seats", json={"seat_ids": [by_num[5]["id"]]}, headers=H)
    check("assign reserved 5 to guest2 pre-RSVP", r.status_code == 200, r.text)
    client.post(f"/public/rsvp/{guest2['rsvp_token']}/respond", json={"attending": True})
    nums, n_tickets = db_ticket_seats(guest2["id"])
    check("guest2 mints 1 ticket stamped 5", n_tickets == 1 and nums == {5}, f"{n_tickets} {nums}")

    print()
    if failures:
        print("FAILURES:", failures)
        raise SystemExit(1)
    print("guest seats: all clear")


if __name__ == "__main__":
    main()