# eventnxt-backend: test_adjust.py  (verification harness — repo root)
#
# Organizer adjust & upgrade flows (Invites slice E) against a REAL
# Postgres:
#   1. reduce a day's grant + sync → excess code voided, shape matches
#   2. raise a day's grant + sync → exactly the shortfall minted
#   3. DAY SWAP: retarget the grant from D1 to D2 + sync → old day's
#      codes voided, new day's minted
#   4. a seated code survives a shrink while its day keeps quantity
#   5. single-day visit change (D1 → D2) + sync retargets the codes
#   6. sync refuses unconfirmed guests; resend with a note doesn't crash
#      without SMTP
#
# Run: DATABASE_URL="postgresql://test@/eventnxt_test?host=/tmp&port=5433" python3 test_adjust.py
import os
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


def patch_guest(g, gt_id, **over):
    body = {"name": g["name"], "email": g["email"], "guest_type_id": gt_id,
            "seating_category_id": g.get("seating_category_id"), "section_label": g.get("section_label"),
            "allocation_status": "confirmed", "party_size": g.get("party_size", 1),
            "guest_mode": "invite", "hold_timing": "now"}
    body.update(over)
    return client.patch(f"/events/{EV}/guests/{g['id']}", json=body, headers=H)


def sync(gid, note=None):
    return client.post(f"/events/{EV}/guests/{gid}/sync-tickets", json={"note": note, "resend": True}, headers=H)


def main():
    client.patch(f"/events/{EV}/settings", json={"ticket_span": "mixed"}, headers=H)
    gt = client.post(f"/events/{EV}/guest-types", json={"name": "VIP", "guest_mode": "invite"}, headers=H).json()

    g = client.post(f"/events/{EV}/guests",
                    json={"name": "Adjustee", "email": "a@x.com", "guest_type_id": gt["id"],
                          "allocation_status": "confirmed", "party_size": 1, "guest_mode": "invite",
                          "ticket_allotment": [{"date": D1, "quantity": 2}, {"date": D3, "quantity": 2}]},
                    headers=H).json()
    client.post(f"/public/rsvp/{g['rsvp_token']}/respond", json={"attending": True})
    check("baseline mint 2+2", shape_of(g["id"]) == {D1: 2, D3: 2}, shape_of(g["id"]))

    # ---- 6a. unconfirmed guests refused ----
    gp = client.post(f"/events/{EV}/guests",
                     json={"name": "Pending P", "email": "pp@x.com", "guest_type_id": gt["id"],
                           "allocation_status": "pending", "party_size": 1, "guest_mode": "invite",
                           "hold_timing": "on_confirm"},
                     headers=H).json()
    r = sync(gp["id"])
    check("sync refuses unconfirmed", r.status_code == 400, r.text)

    # ---- 1. reduce + sync voids ----
    patch_guest(g, gt["id"], ticket_allotment=[{"date": D1, "quantity": 1}, {"date": D3, "quantity": 2}])
    r = sync(g["id"], note="Small change to your Friday tickets")
    check("sync ok with note", r.status_code == 200, r.text)
    check("reduced shape enforced (excess voided)", shape_of(g["id"]) == {D1: 1, D3: 2}, shape_of(g["id"]))

    # ---- 2. raise + sync mints ----
    patch_guest(g, gt["id"], ticket_allotment=[{"date": D1, "quantity": 1}, {"date": D3, "quantity": 4}])
    sync(g["id"])
    check("raised shape minted", shape_of(g["id"]) == {D1: 1, D3: 4}, shape_of(g["id"]))

    # ---- 3. day swap ----
    patch_guest(g, gt["id"], ticket_allotment=[{"date": D2, "quantity": 3}])
    sync(g["id"])
    check("day swap: D1/D3 voided, D2 minted", shape_of(g["id"]) == {D2: 3}, shape_of(g["id"]))

    # ---- 4. seated code survives shrink on its day ----
    pool = client.post(f"/events/{EV}/seating-categories",
                       json={"name": "Row 1", "capacity": 1, "sales_grain": "seat", "row_label": "Row 1"},
                       headers=H).json()
    client.put(f"/events/{EV}/seating-categories/{pool['id']}/sections",
               json={"sections": [{"section_label": "A", "row_label": "Row 1", "capacity": 3}]}, headers=H)
    client.post(f"/events/{EV}/ticket-types",
                json={"name": "Row 1", "description": None, "price_cents": 0, "quantity": 3, "max_per_order": 3,
                      "admits": 1, "seating_category_id": pool["id"], "valid_date": D2,
                      "sales_start": None, "sales_end": None, "is_active": True, "sort_order": 0}, headers=H)
    patch_guest(g, gt["id"], seating_category_id=pool["id"],
                ticket_allotment=[{"date": D2, "quantity": 3}])
    seats = client.get(f"/events/{EV}/seating-categories/{pool['id']}/seats", headers=H).json()
    client.put(f"/events/{EV}/guests/{g['id']}/seats", json={"seat_ids": [seats[0]["id"]]}, headers=H)
    patch_guest(g, gt["id"], seating_category_id=pool["id"],
                ticket_allotment=[{"date": D2, "quantity": 1}])
    r = sync(g["id"], note="You've been upgraded — Row 1, Seat 1!")
    check("shrink-to-1 with upgrade note ok", r.status_code == 200, r.text)
    from app.database import SessionLocal
    from app.models.ticket import Ticket, TicketStatus

    db = SessionLocal()
    survivors = db.query(Ticket).filter(Ticket.guest_id == g["id"], Ticket.status == TicketStatus.VALID).all()
    db.close()
    check("the surviving code is the seated D2 one",
          len(survivors) == 1 and survivors[0].seat_id is not None and survivors[0].valid_date == D2,
          [(t.valid_date, t.seat_id is not None) for t in survivors])

    # ---- 5. single-day visit change ----
    g2 = client.post(f"/events/{EV}/guests",
                     json={"name": "One Day", "email": "od@x.com", "guest_type_id": gt["id"],
                           "allocation_status": "confirmed", "party_size": 2, "guest_mode": "invite",
                           "visit_date": D1},
                     headers=H).json()
    client.post(f"/public/rsvp/{g2['rsvp_token']}/respond", json={"attending": True})
    check("visit-dated baseline", shape_of(g2["id"]) == {D1: 2}, shape_of(g2["id"]))
    patch_guest(g2, gt["id"], party_size=2, visit_date=D2)
    sync(g2["id"])
    check("visit-day change retargets the codes", shape_of(g2["id"]) == {D2: 2}, shape_of(g2["id"]))

    print()
    if failures:
        print("FAILURES:", failures)
        raise SystemExit(1)
    print("adjust flows: all clear")


if __name__ == "__main__":
    main()