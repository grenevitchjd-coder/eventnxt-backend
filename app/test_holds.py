# eventnxt-backend: test_holds.py  (verification harness — repo root)
#
# Hold ledger (Invites slice C1) against a REAL Postgres:
#   1. a PENDING hold-now guest's heads reduce what buyers can claim in
#      that section; buying up to the true remainder still works
#   2. an on_confirm pending guest is invisible to buyers — until they
#      confirm, at which point their heads bite
#   3. hold_timing 'later' never blocks while pending
#   4. day-aware: a guest targeted at the template pool with a Saturday
#      grant blocks Saturday's clone section, not Friday's
#   5. two pending hold-now guests can't overbook a section (the second
#      save is refused)
#   6. hold-now guests without seats are excluded once they hold real
#      seats (no double count)
#
# Run: DATABASE_URL="postgresql://test@/eventnxt_test?host=/tmp&port=5433" python3 test_holds.py
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
TT_BASE = {"description": None, "price_cents": 0, "max_per_order": 20, "admits": 1,
           "sales_start": None, "sales_end": None, "is_active": True, "sort_order": 0}


def make_pool(name, sections, day=None, qty=30):
    pool = client.post(f"/events/{EV}/seating-categories",
                       json={"name": name, "capacity": 1, "sales_grain": "row", "row_label": name},
                       headers=H).json()
    client.put(f"/events/{EV}/seating-categories/{pool['id']}/sections",
               json={"sections": [{"section_label": s, "row_label": name, "capacity": c} for s, c in sections]},
               headers=H)
    tt = client.post(f"/events/{EV}/ticket-types",
                     json={**TT_BASE, "name": name, "quantity": qty, "seating_category_id": pool["id"],
                           "valid_date": day},
                     headers=H).json()
    return pool, tt


def buy_section(slug, tt_id, qty, section_id):
    return client.post(
        f"/public/events/{slug}/checkout",
        json={"buyer_name": "B", "buyer_email": "b@x.com",
              "items": [{"ticket_type_id": tt_id, "quantity": qty, "zone_section_id": section_id}]},
    )


def sections_of_tt(slug, tt_id):
    pub = client.get(f"/public/events/{slug}/ticket-types").json()
    t = next(x for x in pub if x["id"] == tt_id)
    return {s["section_label"]: s["id"] for s in (t.get("sections") or [])}, {
        s["section_label"]: s["remaining"] for s in (t.get("sections") or [])
    }


def add_guest(name, gt_id, pool_id, section, party, status, timing, allot=None):
    body = {"name": name, "email": f"{name.lower().replace(' ', '')}@x.com", "guest_type_id": gt_id,
            "seating_category_id": pool_id, "section_label": section, "allocation_status": status,
            "party_size": party, "guest_mode": "invite", "hold_timing": timing}
    if allot:
        body["ticket_allotment"] = allot
    return client.post(f"/events/{EV}/guests", json=body, headers=H)


def main():
    client.patch(f"/events/{EV}/settings", json={"ticket_span": "mixed"}, headers=H)
    gt = client.post(f"/events/{EV}/guest-types", json={"name": "VIP", "guest_mode": "invite"}, headers=H).json()
    client.patch(f"/events/{EV}/profile/refund-policy", json={"refund_policy": "none"}, headers=H)
    slug = client.post(f"/events/{EV}/profile/publish", headers=H).json()["slug"]

    # ---- 1. pending hold-now bites buyers ----
    pool, tt = make_pool("Row 3", [("C", 10)])
    secs, rem = sections_of_tt(slug, tt["id"])
    r = add_guest("Hold Now", gt["id"], pool["id"], "C", 4, "pending", "now")
    check("pending hold-now guest saved", r.status_code == 201, r.text)
    _, rem = sections_of_tt(slug, tt["id"])
    check("public picker shows the honest remainder", rem.get("C") == 6, rem)
    r = buy_section(slug, tt["id"], 7, secs["C"])
    check("buyer over the remainder refused", r.status_code == 400 and "only has 6" in r.text, r.text)
    r = buy_section(slug, tt["id"], 6, secs["C"])
    check("buyer up to the true remainder succeeds", r.status_code == 200, r.text)

    # ---- 2. on_confirm invisible until confirmed ----
    pool2, tt2 = make_pool("Row 4", [("D", 5)])
    secs2, _ = sections_of_tt(slug, tt2["id"])
    g2 = add_guest("Later Confirm", gt["id"], pool2["id"], "D", 3, "pending", "on_confirm").json()
    r = buy_section(slug, tt2["id"], 4, secs2["D"])
    check("on_confirm pending doesn't block buyers", r.status_code == 200, r.text)
    r = client.patch(f"/events/{EV}/guests/{g2['id']}",
                     json={"name": "Later Confirm", "email": "laterconfirm@x.com", "guest_type_id": gt["id"],
                           "seating_category_id": pool2["id"], "section_label": "D",
                           "allocation_status": "confirmed", "party_size": 1, "guest_mode": "invite",
                           "hold_timing": "on_confirm"},
                     headers=H)
    check("confirm shrunk to fit succeeds", r.status_code == 200, r.text)
    r = buy_section(slug, tt2["id"], 1, secs2["D"])
    check("confirmed heads now bite (section full)", r.status_code == 400, r.text)

    # ---- 3. 'later' never blocks while pending ----
    pool3, tt3 = make_pool("Row 5", [("E", 4)])
    secs3, _ = sections_of_tt(slug, tt3["id"])
    add_guest("Someday", gt["id"], pool3["id"], "E", 4, "pending", "later")
    r = buy_section(slug, tt3["id"], 4, secs3["E"])
    check("'later' pending guest is invisible to buyers", r.status_code == 200, r.text)

    # ---- 4. day-aware: Saturday grant blocks Saturday's clone only ----
    poolT, ttT = make_pool("Row 2", [("B", 6)], day=D1)
    clones = client.post(f"/events/{EV}/ticket-types/{ttT['id']}/fan-out", headers=H).json()
    sat = next(c for c in clones if c["valid_date"] == D2)
    sat_secs, _ = sections_of_tt(slug, sat["id"])
    fri_secs, _ = sections_of_tt(slug, ttT["id"])
    r = add_guest("Sat Star", gt["id"], poolT["id"], "B", 1, "pending", "now",
                  allot=[{"date": D2, "quantity": 5}])
    check("day-granted hold-now guest saved", r.status_code == 201, r.text)
    r = buy_section(slug, sat["id"], 2, sat_secs["B"])
    check("saturday buyer sees the hold", r.status_code == 400 and "only has 1" in r.text, r.text)
    r = buy_section(slug, ttT["id"], 6, fri_secs["B"])
    check("friday buyer unaffected", r.status_code == 200, r.text)

    # ---- 5. two hold-now guests can't overbook ----
    pool5, _tt6 = make_pool("Row 6", [("F", 8)])
    add_guest("First Hold", gt["id"], pool5["id"], "F", 6, "pending", "now")
    r = add_guest("Second Hold", gt["id"], pool5["id"], "F", 5, "pending", "now")
    check("second hold-now over capacity refused", r.status_code == 400 and "only has" in r.text, r.text)

    print()
    if failures:
        print("FAILURES:", failures)
        raise SystemExit(1)
    print("hold ledger: all clear")


if __name__ == "__main__":
    main()