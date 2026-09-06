# eventnxt-backend: test/test_external_rooms.py  (verification harness)
#
# Slice 2 of the external-events work: day machinery WITHOUT ticket
# types. Two pieces, both pinned here:
#
#   A. POOL fan-out (POST /seating-categories/{id}/fan-out): clones a
#      bare pool to every event day — "<Base> (MM/DD)" for days 2..N,
#      the bare base serving the first night — with sections and fresh
#      seats, reserved holds NOT copied, idempotent, and refusing both
#      suffixed clones and pools sold by a ticket type.
#   B. pool_for_day's name-family fallback: with ZERO ticket types,
#      priorities configured once against any family member resolve a
#      dated guest into the pool serving THEIR night, day capacities
#      stay independent, and a lone un-fanned pool keeps today's
#      shared-room behavior.
#
# Run: DATABASE_URL="postgresql://test@/eventnxt_test?host=/tmp&port=5433" python3 test/test_external_rooms.py
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

email_mod.send_email = lambda **kw: None

EV, ORG = str(uuid.uuid4()), str(uuid.uuid4())
D1, D2, D3 = "2026-10-08", "2026-10-09", "2026-10-10"
failures = []


def check(name, cond, extra=""):
    print(("  ok " if cond else "  ✗ ") + name + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


class U:
    user_id = "u"; organization_id = ORG; name = "T"; email = "t@x.com"; role = "owner"; raw_token = "tok"
    event_data = {"organization_id": ORG, "name": "Ext Fest", "start_date": D1, "end_date": D3}


app.dependency_overrides[get_current_user] = lambda: U()
app.dependency_overrides[require_event_access] = lambda event_id: U()
c = TestClient(app)
H = {"Authorization": "Bearer tok"}


def add_guest(gt_id, name, party, visit_date=None, status="confirmed"):
    payload = {"name": name, "email": f"{name.replace(' ', '').lower()}@x.com", "guest_type_id": gt_id,
               "allocation_status": status, "party_size": party}
    if visit_date:
        payload["visit_date"] = visit_date
    return c.post(f"/events/{EV}/guests", json=payload, headers=H)


def pools_by_name():
    return {p["name"]: p for p in c.get(f"/events/{EV}/seating-categories", headers=H).json()}


def main():
    # ---- bootstrap: EXTERNAL event, per-day span, zero ticket types ----
    c.patch(f"/events/{EV}/settings",
            json={"ticketing_mode": "external", "sales_source": "csv", "ticket_span": "per_day"}, headers=H)

    row1 = c.post(f"/events/{EV}/seating-categories",
                  json={"name": "Row 1 Patron", "capacity": 1, "sales_grain": "seat", "row_label": "Row 1"},
                  headers=H).json()
    c.put(f"/events/{EV}/seating-categories/{row1['id']}/sections",
          json={"sections": [
              {"section_label": "A", "row_label": "Row 1", "capacity": 2},
              {"section_label": "B", "row_label": "Row 1", "capacity": 3},
          ]}, headers=H)
    # a reserved hold on the base — must NOT be copied by fan-out
    base_seats = c.get(f"/events/{EV}/seating-categories/{row1['id']}/seats", headers=H).json()
    c.post(f"/events/{EV}/seating-categories/{row1['id']}/seats/block",
           json={"seat_ids": [base_seats[0]["id"]], "label": "Press"}, headers=H)

    # ---- A1. fan-out: clones for days 2..3 with sections + fresh seats ----
    r = c.post(f"/events/{EV}/seating-categories/{row1['id']}/fan-out", headers=H)
    check("fan-out returns 200 with 2 clones", r.status_code == 200 and len(r.json()) == 2, r.text)
    ps = pools_by_name()
    check("clones named (10/09) and (10/10), base stays bare",
          "Row 1 Patron (10/09)" in ps and "Row 1 Patron (10/10)" in ps and "Row 1 Patron" in ps, list(ps))
    clone2, clone3 = ps.get("Row 1 Patron (10/09)"), ps.get("Row 1 Patron (10/10)")
    check("clone capacity derives from sections (5)", clone2 and clone2["capacity"] == 5, clone2)
    check("clone sections copied", clone2 and sorted(s["section_label"] for s in clone2["sections"]) == ["A", "B"])
    c2_seats = c.get(f"/events/{EV}/seating-categories/{clone2['id']}/seats", headers=H).json()
    check("clone has fresh seats (5)", len(c2_seats) == 5, len(c2_seats))
    check("reserved hold NOT copied onto the clone", all(s["status"] == "available" for s in c2_seats))

    # ---- A2. idempotent; refusals ----
    r2 = c.post(f"/events/{EV}/seating-categories/{row1['id']}/fan-out", headers=H)
    check("second fan-out creates nothing", r2.status_code == 200 and r2.json() == [], r2.text)
    r3 = c.post(f"/events/{EV}/seating-categories/{clone2['id']}/fan-out", headers=H)
    check("fanning a suffixed clone refuses", r3.status_code == 400 and "base area" in r3.json()["detail"], r3.text)

    # ---- B1. name-family day routing with ZERO ticket types ----
    gt = c.post(f"/events/{EV}/guest-types", json={"name": "Celebs", "guest_mode": "invite"}, headers=H).json()
    # priorities configured ONCE, against the SECOND night's clone — any
    # member should do, exactly like the ticket-type families.
    c.post(f"/events/{EV}/guest-types/{gt['id']}/seating-priorities",
           json={"seating_category_id": clone2["id"], "section_label": "A"}, headers=H)
    c.post(f"/events/{EV}/guest-types/{gt['id']}/seating-priorities",
           json={"seating_category_id": clone2["id"]}, headers=H)

    g_d3 = add_guest(gt["id"], "Sat Celeb", 1, visit_date=D3).json()
    check("day-3 guest lands in the (10/10) clone, section A",
          g_d3["seating_category_id"] == clone3["id"] and g_d3["section_label"] == "A", g_d3)
    g_d1 = add_guest(gt["id"], "Thu Celeb", 1, visit_date=D1).json()
    check("day-1 guest lands in the BARE base pool (first night)",
          g_d1["seating_category_id"] == row1["id"] and g_d1["section_label"] == "A", g_d1)
    g_d2 = add_guest(gt["id"], "Fri Celeb", 1, visit_date=D2).json()
    check("day-2 guest lands in the (10/09) clone",
          g_d2["seating_category_id"] == clone2["id"] and g_d2["section_label"] == "A", g_d2)

    # ---- B2. per-day capacities independent: fill day-2 A, day-3 A open ----
    g_d2b = add_guest(gt["id"], "Fri Two", 1, visit_date=D2).json()
    check("day-2 A takes its second head", g_d2b["section_label"] == "A", g_d2b)
    g_d2c = add_guest(gt["id"], "Fri Three", 1, visit_date=D2).json()
    check("day-2 A full -> day-2 pool level (not day 3)",
          g_d2c["seating_category_id"] == clone2["id"] and g_d2c["section_label"] is None, g_d2c)
    g_d3b = add_guest(gt["id"], "Sat Two", 1, visit_date=D3).json()
    check("day-3 A unaffected by day-2 filling", g_d3b["section_label"] == "A", g_d3b)

    # ---- B3. undated guest stays at the priority's own pool ----
    g_nd = add_guest(gt["id"], "No Date", 1).json()
    check("undated guest -> the configured pool itself",
          g_nd["seating_category_id"] == clone2["id"], g_nd)

    # ---- B4. a LONE pool (never fanned out) keeps shared-room behavior ----
    lounge = c.post(f"/events/{EV}/seating-categories",
                    json={"name": "Champagne Lounge", "capacity": 10, "sales_grain": "ga"}, headers=H).json()
    gt2 = c.post(f"/events/{EV}/guest-types", json={"name": "Lounge", "guest_mode": "invite"}, headers=H).json()
    c.post(f"/events/{EV}/guest-types/{gt2['id']}/seating-priorities",
           json={"seating_category_id": lounge["id"]}, headers=H)
    l1 = add_guest(gt2["id"], "Lounge Thu", 1, visit_date=D1).json()
    l2 = add_guest(gt2["id"], "Lounge Sat", 1, visit_date=D3).json()
    check("lone pool serves every day (no phantom family)",
          l1["seating_category_id"] == lounge["id"] and l2["seating_category_id"] == lounge["id"], (l1, l2))

    # ---- A3. fan-out refuses a pool sold by a ticket type ----
    tt_pool = c.post(f"/events/{EV}/seating-categories",
                     json={"name": "Native GA", "capacity": 20, "sales_grain": "ga"}, headers=H).json()
    c.post(f"/events/{EV}/ticket-types",
           json={"name": "Native GA", "description": None, "price_cents": 1000, "quantity": 20,
                 "max_per_order": 10, "admits": 1, "seating_category_id": tt_pool["id"], "valid_date": D1,
                 "sales_start": None, "sales_end": None, "is_active": True, "sort_order": 0}, headers=H)
    r4 = c.post(f"/events/{EV}/seating-categories/{tt_pool['id']}/fan-out", headers=H)
    check("pool sold by a ticket type refuses pool fan-out",
          r4.status_code == 400 and "ticket type" in r4.json()["detail"], r4.text)

    print()
    if failures:
        print(f"external rooms: {len(failures)} FAILURES: {failures}")
        sys.exit(1)
    print("external rooms: all clear")


if __name__ == "__main__":
    main()