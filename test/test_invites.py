# eventnxt-backend: test/test_invites.py  (verification harness)
#
# Invite per-day grants (Invites slice A) against a REAL Postgres:
#   1. an invite-mode guest granted {D1: 2, D3: 4} mints exactly 2 dated
#      D1 codes + 4 dated D3 codes on confirm — nothing for D2
#   2. re-confirm is idempotent; raising D3 to 5 tops up exactly one code
#   3. a hand-assigned seat in D3's pool stamps a D3 code only
#   4. grant days outside the event's range are refused (400)
#   5. a distributor's allotment is a BUDGET, not self-tickets: their own
#      confirm mints party_size codes, not the allotment shape
#   6. select-mode guests still see their allotment days as choices
#
# Run: DATABASE_URL="postgresql://test@/eventnxt_test?host=/tmp&port=5433" python3 test/test_invites.py
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


def codes_by_day(token):
    data = client.get(f"/public/rsvp/{token}").json()
    tickets = data.get("tickets") or []
    if tickets and isinstance(tickets[0], dict):
        out = {}
        for t in tickets:
            out.setdefault(t.get("valid_date"), []).append(t.get("code"))
        return out
    # fall back to raw codes without dates
    return {None: data.get("ticket_codes") or []}


def main():
    client.patch(f"/events/{EV}/settings", json={"ticket_span": "mixed"}, headers=H)
    gt = client.post(f"/events/{EV}/guest-types", json={"name": "Celebrity", "guest_mode": "invite"}, headers=H).json()

    # ---- 4. day validation first (cheap) ----
    r = client.post(f"/events/{EV}/guests",
                    json={"name": "Bad Day", "email": "bad@x.com", "guest_type_id": gt["id"],
                          "allocation_status": "pending", "party_size": 1, "guest_mode": "invite",
                          "ticket_allotment": [{"date": "2026-12-25", "quantity": 2}]},
                    headers=H)
    check("grant day outside event refused", r.status_code == 400 and "isn" in r.text, r.text)

    # ---- 1. per-day grant mints its exact shape ----
    g = client.post(f"/events/{EV}/guests",
                    json={"name": "Star", "email": "star@x.com", "guest_type_id": gt["id"],
                          "allocation_status": "confirmed", "party_size": 1, "guest_mode": "invite",
                          "ticket_allotment": [{"date": D1, "quantity": 2}, {"date": D3, "quantity": 4}]},
                    headers=H).json()
    r = client.post(f"/public/rsvp/{g['rsvp_token']}/respond", json={"attending": True})
    check("rsvp yes ok", r.status_code == 200, r.text)
    from app.database import SessionLocal
    from app.models.ticket import Ticket, TicketStatus

    db = SessionLocal()
    rows = db.query(Ticket.valid_date).filter(Ticket.guest_id == g["id"], Ticket.status == TicketStatus.VALID).all()
    db.close()
    shape = {}
    for (vd,) in rows:
        shape[vd] = shape.get(vd, 0) + 1
    check("mints 2×D1 + 4×D3, nothing for D2", shape == {D1: 2, D3: 4}, shape)

    # ---- 2. idempotent + targeted top-up ----
    client.post(f"/public/rsvp/{g['rsvp_token']}/respond", json={"attending": True})
    db = SessionLocal()
    n = db.query(Ticket).filter(Ticket.guest_id == g["id"], Ticket.status == TicketStatus.VALID).count()
    db.close()
    check("re-confirm mints nothing", n == 6, n)
    r = client.patch(f"/events/{EV}/guests/{g['id']}",
                     json={"name": "Star", "email": "star@x.com", "guest_type_id": gt["id"],
                           "allocation_status": "confirmed", "party_size": 1, "guest_mode": "invite",
                           "ticket_allotment": [{"date": D1, "quantity": 2}, {"date": D3, "quantity": 5}]},
                     headers=H)
    check("allotment update accepted", r.status_code == 200, r.text)
    client.post(f"/public/rsvp/{g['rsvp_token']}/respond", json={"attending": True})
    db = SessionLocal()
    rows = db.query(Ticket.valid_date).filter(Ticket.guest_id == g["id"], Ticket.status == TicketStatus.VALID).all()
    db.close()
    shape = {}
    for (vd,) in rows:
        shape[vd] = shape.get(vd, 0) + 1
    check("raising D3 to 5 tops up exactly one D3 code", shape == {D1: 2, D3: 5}, shape)

    # ---- 3. seat in D3's pool stamps a D3 code ----
    pool = client.post(f"/events/{EV}/seating-categories",
                       json={"name": "Row 1", "capacity": 1, "sales_grain": "seat", "row_label": "Row 1"},
                       headers=H).json()
    client.put(f"/events/{EV}/seating-categories/{pool['id']}/sections",
               json={"sections": [{"section_label": "A", "row_label": "Row 1", "capacity": 3}]}, headers=H)
    tt = client.post(f"/events/{EV}/ticket-types",
                     json={**TT_BASE, "name": "Row 1", "quantity": 3, "seating_category_id": pool["id"], "valid_date": D1},
                     headers=H).json()
    clones = client.post(f"/events/{EV}/ticket-types/{tt['id']}/fan-out", headers=H).json()
    d3_pool = next(c["seating_category_id"] for c in clones if c["valid_date"] == D3)
    d3_seats = client.get(f"/events/{EV}/seating-categories/{d3_pool}/seats", headers=H).json()
    client.patch(f"/events/{EV}/guests/{g['id']}",
                 json={"name": "Star", "email": "star@x.com", "guest_type_id": gt["id"],
                       "seating_category_id": pool["id"], "allocation_status": "confirmed",
                       "party_size": 1, "guest_mode": "invite"},
                 headers=H)
    r = client.put(f"/events/{EV}/guests/{g['id']}/seats", json={"seat_ids": [d3_seats[0]["id"]]}, headers=H)
    check("seat assignment ok", r.status_code == 200, r.text)
    db = SessionLocal()
    stamped = db.query(Ticket).filter(Ticket.guest_id == g["id"], Ticket.seat_id.isnot(None),
                                      Ticket.status == TicketStatus.VALID).all()
    db.close()
    check("seat stamped exactly one code, and it's a D3 code",
          len(stamped) == 1 and stamped[0].valid_date == D3, [(t.code, t.valid_date) for t in stamped])

    # ---- 5. distributor allotment stays a budget ----
    gt2 = client.post(f"/events/{EV}/guest-types", json={"name": "Sponsor", "guest_mode": "distribute"}, headers=H).json()
    sp = client.post(f"/events/{EV}/guests",
                     json={"name": "Sponsor Co", "email": "sp@x.com", "guest_type_id": gt2["id"],
                           "allocation_status": "confirmed", "party_size": 1, "guest_mode": "distribute",
                           "ticket_allotment": [{"date": D1, "quantity": 10}, {"date": D2, "quantity": 10}]},
                     headers=H).json()
    client.post(f"/public/rsvp/{sp['rsvp_token']}/respond", json={"attending": True})
    db = SessionLocal()
    n = db.query(Ticket).filter(Ticket.guest_id == sp["id"], Ticket.status == TicketStatus.VALID).count()
    db.close()
    check("distributor's own confirm mints party_size (not 20)", n <= 3, n)

    # ---- 6. select-mode: allotment days remain the choices ----
    gt3 = client.post(f"/events/{EV}/guest-types", json={"name": "Volunteer", "guest_mode": "select"}, headers=H).json()
    vo = client.post(f"/events/{EV}/guests",
                     json={"name": "Vol", "email": "vol@x.com", "guest_type_id": gt3["id"],
                           "allocation_status": "confirmed", "party_size": 1, "guest_mode": "select",
                           "ticket_allotment": [{"date": D1, "quantity": 1}, {"date": D2, "quantity": 1}]},
                     headers=H).json()
    extras = client.get(f"/public/rsvp/{vo['rsvp_token']}").json()
    check("select-mode guest sees its days as choices",
          sorted(extras.get("available_days") or []) == [D1, D2], extras.get("available_days"))

    print()
    if failures:
        print("FAILURES:", failures)
        raise SystemExit(1)
    print("invite day grants: all clear")


if __name__ == "__main__":
    main()