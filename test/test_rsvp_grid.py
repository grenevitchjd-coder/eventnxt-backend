# eventnxt-backend: test/test_rsvp_grid.py  (verification harness)
#
# Invite RSVP per-day grid (Invites slice B) against a REAL Postgres:
#   1. GET exposes day_grants for an invite guest
#   2. accepting fewer ({D1:2, D3:1} of a {D1:2, D3:4} grant) mints only
#      the accepted shape and rewrites the grant
#   3. accepting fewer AFTER a full mint voids the excess codes (and a
#      seated code survives while its day keeps tickets)
#   4. asking for more through the grid is refused with a pointer to the
#      request flow; day outside the grant refused
#   5. select mode: total tickets spread across offered days, per-day cap
#      and party total enforced; single-day pick sets visit_date
#   6. request-tickets with a day + approve bumps that day's grant and
#      mints exactly that day's top-up
#
# Run: DATABASE_URL="postgresql://test@/eventnxt_test?host=/tmp&port=5433" python3 test/test_rsvp_grid.py
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


def shape_of(guest_id):
    from app.database import SessionLocal
    from app.models.ticket import Ticket, TicketStatus

    db = SessionLocal()
    rows = db.query(Ticket.valid_date).filter(Ticket.guest_id == guest_id, Ticket.status == TicketStatus.VALID).all()
    db.close()
    out = {}
    for (vd,) in rows:
        out[vd] = out.get(vd, 0) + 1
    return out


def main():
    client.patch(f"/events/{EV}/settings", json={"ticket_span": "mixed"}, headers=H)
    gt = client.post(f"/events/{EV}/guest-types", json={"name": "Celebrity", "guest_mode": "invite"}, headers=H).json()

    # ---- 1 + 2: fewer via the grid ----
    g = client.post(f"/events/{EV}/guests",
                    json={"name": "Star", "email": "star@x.com", "guest_type_id": gt["id"],
                          "allocation_status": "confirmed", "party_size": 1, "guest_mode": "invite",
                          "ticket_allotment": [{"date": D1, "quantity": 2}, {"date": D3, "quantity": 4}]},
                    headers=H).json()
    info = client.get(f"/public/rsvp/{g['rsvp_token']}").json()
    grants = {x["date"]: x["quantity"] for x in (info.get("day_grants") or [])}
    check("GET exposes the per-day grant", grants == {D1: 2, D3: 4}, info.get("day_grants"))
    r = client.post(f"/public/rsvp/{g['rsvp_token']}/respond",
                    json={"attending": True, "day_quantities": {D1: 2, D3: 1}})
    check("fewer accepted", r.status_code == 200, r.text)
    check("mints only the accepted shape", shape_of(g["id"]) == {D1: 2, D3: 1}, shape_of(g["id"]))
    info = client.get(f"/public/rsvp/{g['rsvp_token']}").json()
    grants = {x["date"]: x["quantity"] for x in (info.get("day_grants") or [])}
    check("grant rewritten to the accepted shape", grants == {D1: 2, D3: 1}, grants)

    # ---- 4: more via grid refused; unknown day refused ----
    r = client.post(f"/public/rsvp/{g['rsvp_token']}/respond",
                    json={"attending": True, "day_quantities": {D1: 5}})
    check("grid can't grow a grant", r.status_code == 400 and "request" in r.text, r.text)
    r = client.post(f"/public/rsvp/{g['rsvp_token']}/respond",
                    json={"attending": True, "day_quantities": {D2: 1}})
    check("day outside the grant refused", r.status_code == 400, r.text)

    # ---- 3: shrink after full mint, seated code survives ----
    g2 = client.post(f"/events/{EV}/guests",
                     json={"name": "Duo", "email": "duo@x.com", "guest_type_id": gt["id"],
                           "allocation_status": "confirmed", "party_size": 1, "guest_mode": "invite",
                           "ticket_allotment": [{"date": D1, "quantity": 3}]},
                     headers=H).json()
    client.post(f"/public/rsvp/{g2['rsvp_token']}/respond", json={"attending": True})
    check("full accept mints 3", shape_of(g2["id"]) == {D1: 3}, shape_of(g2["id"]))
    pool = client.post(f"/events/{EV}/seating-categories",
                       json={"name": "Row 2", "capacity": 1, "sales_grain": "seat", "row_label": "Row 2"},
                       headers=H).json()
    client.put(f"/events/{EV}/seating-categories/{pool['id']}/sections",
               json={"sections": [{"section_label": "B", "row_label": "Row 2", "capacity": 4}]}, headers=H)
    client.post(f"/events/{EV}/ticket-types",
                json={"name": "Row 2", "description": None, "price_cents": 0, "quantity": 4, "max_per_order": 4,
                      "admits": 1, "seating_category_id": pool["id"], "valid_date": D1,
                      "sales_start": None, "sales_end": None, "is_active": True, "sort_order": 0},
                headers=H)
    client.patch(f"/events/{EV}/guests/{g2['id']}",
                 json={"name": "Duo", "email": "duo@x.com", "guest_type_id": gt["id"],
                       "seating_category_id": pool["id"], "allocation_status": "confirmed",
                       "party_size": 1, "guest_mode": "invite"},
                 headers=H)
    seats = client.get(f"/events/{EV}/seating-categories/{pool['id']}/seats", headers=H).json()
    client.put(f"/events/{EV}/guests/{g2['id']}/seats", json={"seat_ids": [seats[0]["id"]]}, headers=H)
    r = client.post(f"/public/rsvp/{g2['rsvp_token']}/respond",
                    json={"attending": True, "day_quantities": {D1: 1}})
    check("shrink to 1 accepted", r.status_code == 200, r.text)
    check("excess voided down to 1", shape_of(g2["id"]) == {D1: 1}, shape_of(g2["id"]))
    from app.database import SessionLocal
    from app.models.ticket import Ticket, TicketStatus

    db = SessionLocal()
    survivor = db.query(Ticket).filter(Ticket.guest_id == g2["id"], Ticket.status == TicketStatus.VALID).first()
    db.close()
    check("the surviving code is the seated one", survivor.seat_id is not None, survivor.seat_id)

    # ---- 5: select mode spread ----
    gt2 = client.post(f"/events/{EV}/guest-types", json={"name": "Volunteer", "guest_mode": "select"}, headers=H).json()
    v = client.post(f"/events/{EV}/guests",
                    json={"name": "Vol", "email": "vol@x.com", "guest_type_id": gt2["id"],
                          "allocation_status": "confirmed", "party_size": 2, "guest_mode": "select",
                          "ticket_allotment": [{"date": D1, "quantity": 2}, {"date": D2, "quantity": 2}]},
                    headers=H).json()
    r = client.post(f"/public/rsvp/{v['rsvp_token']}/respond",
                    json={"attending": True, "day_quantities": {D1: 2, D2: 1}})
    check("select over party total refused", r.status_code == 400 and "adds up" in r.text, r.text)
    r = client.post(f"/public/rsvp/{v['rsvp_token']}/respond",
                    json={"attending": True, "day_quantities": {D1: 1, D2: 1}})
    check("select 1+1 accepted", r.status_code == 200, r.text)
    check("select mints the spread", shape_of(v["id"]) == {D1: 1, D2: 1}, shape_of(v["id"]))
    v2 = client.post(f"/events/{EV}/guests",
                     json={"name": "Vol2", "email": "vol2@x.com", "guest_type_id": gt2["id"],
                           "allocation_status": "confirmed", "party_size": 2, "guest_mode": "select",
                           "ticket_allotment": [{"date": D1, "quantity": 2}, {"date": D2, "quantity": 2}]},
                     headers=H).json()
    client.post(f"/public/rsvp/{v2['rsvp_token']}/respond",
                json={"attending": True, "day_quantities": {D2: 2}})
    from app.models.guest import Guest

    db = SessionLocal()
    vd = db.query(Guest.visit_date).filter(Guest.id == v2["id"]).scalar()
    db.close()
    check("single-day select sets visit_date", vd == D2, vd)

    # ---- 6: request more for a day, approve, targeted top-up ----
    r = client.post(f"/public/rsvp/{g2['rsvp_token']}/request-tickets",
                    json={"quantity": 2, "date": D1, "note": "two more please"})
    check("day request lands", r.status_code == 200, r.text)
    reqs = client.get(f"/events/{EV}/guests/ticket-requests/all", headers=H).json()
    mine = next(x for x in reqs if x["guest_id"] == g2["id"] and x["status"] == "pending")
    check("request carries its day", mine["date"] == D1, mine)
    r = client.post(f"/events/{EV}/guests/ticket-requests/{mine['id']}/approve", headers=H)
    check("approve ok", r.status_code == 200, r.text)
    check("approval topped up that day to 3", shape_of(g2["id"]) == {D1: 3}, shape_of(g2["id"]))

    print()
    if failures:
        print("FAILURES:", failures)
        raise SystemExit(1)
    print("rsvp per-day grid: all clear")


if __name__ == "__main__":
    main()