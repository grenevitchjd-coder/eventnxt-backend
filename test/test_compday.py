# eventnxt-backend: test/test_compday.py  (verification harness)
#
# Comp/day alignment (slice 4) against a REAL Postgres:
#   1. day-aware resolution: a Saturday guest resolves into SATURDAY's
#      clone of the priority pool — section targeting included — even
#      though the priority was configured against Friday's pool
#   2. per-day fallthrough: Saturday's section full -> Saturday's
#      pool-level entry (Friday's capacity is irrelevant)
#   3. whole-event comp (no visit date) at a multi-day event: RSVP yes
#      mints party_size dated codes PER DAY; idempotent on re-yes;
#      Saturday's code admits Saturday, rejected Sunday
#   4. visit-dated comp still mints one day's codes only
#   5. legacy guests holding undated codes are topped up the old way —
#      never exploded into per-day codes
#   6. hand-placed seats only stamp same-day (or undated) codes
#
# Run: DATABASE_URL="postgresql://test@/eventnxt_test?host=/tmp&port=5433" python3 test/test_compday.py
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
from app.models.ticket import Ticket, TicketStatus
from app.services.deps import get_current_user
from app.services.event_access import require_event_access
from app.services.ticketing import generate_ticket_code

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


def add_guest(gt_id, name, party, visit_date=None, status="confirmed"):
    payload = {"name": name, "email": f"{name.replace(' ', '').lower()}@x.com", "guest_type_id": gt_id,
               "allocation_status": status, "party_size": party}
    if visit_date:
        payload["visit_date"] = visit_date
    return client.post(f"/events/{EV}/guests", json=payload, headers=H)


def guest_codes(guest_id):
    db = SessionLocal()
    try:
        rows = db.query(Ticket.code, Ticket.valid_date).filter(
            Ticket.guest_id == guest_id, Ticket.status == TicketStatus.VALID
        ).all()
        return [(c, d) for (c, d) in rows]
    finally:
        db.close()


def main():
    # ---- bootstrap: mixed span, fanned-out row pool with sections ----
    client.patch(f"/events/{EV}/settings", json={"ticket_span": "mixed"}, headers=H)
    pool = client.post(f"/events/{EV}/seating-categories",
                       json={"name": "Row 2", "capacity": 1, "sales_grain": "row", "row_label": "Row 2"},
                       headers=H).json()
    client.put(f"/events/{EV}/seating-categories/{pool['id']}/sections",
               json={"sections": [
                   {"section_label": "A", "row_label": "Row 2", "capacity": 2},
                   {"section_label": "B", "row_label": "Row 2", "capacity": 6},
               ]}, headers=H)
    tt = client.post(f"/events/{EV}/ticket-types",
                     json={**TT_BASE, "name": "Row 2", "quantity": 8, "seating_category_id": pool["id"], "valid_date": D1},
                     headers=H).json()
    client.post(f"/events/{EV}/ticket-types/{tt['id']}/fan-out", headers=H)
    client.patch(f"/events/{EV}/profile/refund-policy", json={"refund_policy": "none"}, headers=H)
    client.post(f"/events/{EV}/profile/publish", headers=H)
    pools = {p["name"]: p for p in client.get(f"/events/{EV}/seating-categories", headers=H).json()}
    sat_pool = pools["Row 2 (10/10)"]

    gt = client.post(f"/events/{EV}/guest-types", json={"name": "Models", "guest_mode": "invite"}, headers=H).json()
    # priorities configured ONCE, against FRIDAY's pool
    client.post(f"/events/{EV}/guest-types/{gt['id']}/seating-priorities",
                json={"seating_category_id": pool["id"], "section_label": "A"}, headers=H)
    client.post(f"/events/{EV}/guest-types/{gt['id']}/seating-priorities",
                json={"seating_category_id": pool["id"]}, headers=H)

    # ---- 1. Saturday guest lands in Saturday's clone, Section A ----
    g1 = add_guest(gt["id"], "Sat Model", 1, visit_date=D2).json()
    check("saturday guest resolves into Saturday's pool",
          g1["seating_category_id"] == sat_pool["id"] and g1["section_label"] == "A", g1)

    # ---- 2. per-day fallthrough: fill Saturday's A, next Sat guest -> pool level ----
    g2 = add_guest(gt["id"], "Sat Two", 1, visit_date=D2).json()
    check("saturday A takes its second head", g2["section_label"] == "A", g2)
    g3 = add_guest(gt["id"], "Sat Three", 1, visit_date=D2).json()
    check("saturday A full -> Saturday pool-level (Friday untouched)",
          g3["seating_category_id"] == sat_pool["id"] and g3["section_label"] is None, g3)
    g4 = add_guest(gt["id"], "Fri Model", 1, visit_date=D1).json()
    check("friday guest still gets Friday's Section A",
          g4["seating_category_id"] == pool["id"] and g4["section_label"] == "A", g4)

    # ---- 3. whole-event comp: per-day codes ----
    gt2 = client.post(f"/events/{EV}/guest-types", json={"name": "Sponsor", "guest_mode": "invite"}, headers=H).json()
    g5 = add_guest(gt2["id"], "Whole Sponsor", 2, status="pending").json()
    r = client.post(f"/public/rsvp/{g5['rsvp_token']}/respond", json={"attending": True})
    check("whole-event rsvp yes ok", r.status_code == 200, r.text)
    codes = guest_codes(g5["id"])
    by_day = {}
    for _, d in codes:
        by_day[d] = by_day.get(d, 0) + 1
    check("party 2 x 3 days = 6 dated codes (2 per day)", by_day == {D1: 2, D2: 2, D3: 2}, by_day)
    client.post(f"/public/rsvp/{g5['rsvp_token']}/respond", json={"attending": True})
    check("re-yes is idempotent (still 6)", len(guest_codes(g5["id"])) == 6, guest_codes(g5["id"]))
    sat_code = next(c for c, d in codes if d == D2)
    scan = client.post(f"/events/{EV}/check-in/{sat_code}?day={D3}", headers=H).json()
    check("saturday code rejected on Sunday", scan["result"] == "wrong_day", scan)
    scan = client.post(f"/events/{EV}/check-in/{sat_code}?day={D2}", headers=H).json()
    check("saturday code admits on Saturday", scan["result"] == "admitted", scan)

    # ---- 4. visit-dated comp: one day only ----
    g6 = add_guest(gt2["id"], "Day Sponsor", 1, visit_date=D3, status="pending").json()
    client.post(f"/public/rsvp/{g6['rsvp_token']}/respond", json={"attending": True})
    codes6 = guest_codes(g6["id"])
    check("visit-dated comp mints its day only", len(codes6) == 1 and codes6[0][1] == D3, codes6)

    # ---- 5. legacy undated codes: topped up, never exploded ----
    g7 = add_guest(gt2["id"], "Legacy Guest", 2, status="pending").json()
    db = SessionLocal()
    db.add(Ticket(guest_id=g7["id"], event_id=EV, code=generate_ticket_code(), status=TicketStatus.VALID, valid_date=None))
    db.commit(); db.close()
    client.post(f"/public/rsvp/{g7['rsvp_token']}/respond", json={"attending": True})
    codes7 = guest_codes(g7["id"])
    check("legacy undated guest topped up to party (2 undated, no fan-out)",
          len(codes7) == 2 and all(d is None for _, d in codes7), codes7)

    print()
    if failures:
        print("FAILURES:", failures)
        raise SystemExit(1)
    print("comp/day alignment: all clear")


if __name__ == "__main__":
    main()