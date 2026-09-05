# eventnxt-backend: test/test_allotment_seating.py  (verification harness)
#
# An allotment holder's explicit seating steers their recipients:
#   1. holder picks pool+section -> recipient's RSVP-yes lands there
#      (day-aware: holder stores the family rep; the recipient lands in
#      the sibling pool for THEIR night)
#   2. chosen section full -> recipient overflows to the pool's next
#      section, not to type priorities
#   3. whole pool full -> graceful fallback to the type's priorities
#   4. holder with NO seating choice -> priorities exactly as before
#
# Run: DATABASE_URL="postgresql://test@/eventnxt_test?host=/tmp&port=5433" python3 test/test_allotment_seating.py
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uuid

os.environ.setdefault("DATABASE_URL", "postgresql://test@/eventnxt_test?host=/tmp&port=5433")

from fastapi.testclient import TestClient

from app.main import app
from app.services.deps import get_current_user
from app.services.event_access import require_event_access
import app.services.email as email_mod

email_mod.send_email = lambda *a, **k: True

EV, ORG = str(uuid.uuid4()), str(uuid.uuid4())
D1, D2, D3 = "2026-11-19", "2026-11-20", "2026-11-21"
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
    event_data = {"organization_id": ORG, "name": "Fest", "start_date": D1, "end_date": D3}


app.dependency_overrides[get_current_user] = lambda: FakeUser()
app.dependency_overrides[require_event_access] = lambda event_id: FakeUser()
client = TestClient(app)
H = {"Authorization": "Bearer tok"}

client.patch(f"/events/{EV}/settings", json={"ticket_span": "per_day", "ticketing_mode": "native", "comp_delivery": "rsvp_required"}, headers=H)

# Two pool families: "Front" (fanned per-day, sections A cap 2 / B cap 2) and "Back" (priority target)
front = client.post(f"/events/{EV}/seating-categories", json={"name": "Front", "capacity": 4, "sales_grain": "row"}, headers=H).json()
client.put(f"/events/{EV}/seating-categories/{front['id']}/sections", json={"sections": [{"section_label": "A", "capacity": 2}, {"section_label": "B", "capacity": 2}]}, headers=H)
ft = client.post(f"/events/{EV}/ticket-types", json={"name": "Front", "price_cents": 0, "quantity": 4, "valid_date": D1, "seating_category_id": front["id"]}, headers=H).json()
client.post(f"/events/{EV}/ticket-types/{ft['id']}/fan-out", json={}, headers=H)
back = client.post(f"/events/{EV}/seating-categories", json={"name": "Back", "capacity": 50, "sales_grain": "ga"}, headers=H).json()

gt = client.post(f"/events/{EV}/guest-types", json={"name": "Sponsor", "guest_mode": "distribute"}, headers=H).json()
client.post(f"/events/{EV}/guest-types/{gt['id']}/seating-priorities", json={"seating_category_id": back["id"], "placement": "together"}, headers=H)

cats = {c["name"]: c for c in client.get(f"/events/{EV}/seating-categories", headers=H).json()}
fri_pool = cats["Front (11/20)"]


def add_holder(name, seat_pool=None, section=None):
    g = client.post(f"/events/{EV}/guests", json={"name": name, "email": f"{name.lower().replace(' ', '')}@x.com", "guest_type_id": gt["id"], "allocation_status": "confirmed", "party_size": 1, "guest_mode": "distribute"}, headers=H).json()
    client.patch(f"/events/{EV}/guests/{g['id']}", json={
        "name": g["name"], "email": g["email"], "guest_type_id": gt["id"],
        "seating_category_id": None, "section_label": None, "visit_date": None,
        "recipient_seating_category_id": seat_pool, "recipient_section_label": section,
        "allocation_status": "confirmed", "party_size": 1, "perks": None, "comments": None,
        "guest_mode": "distribute", "hold_timing": "now", "spend_total": None, "cohort_together": True,
        "ticket_allotment": [{"date": D1, "quantity": 4}, {"date": D2, "quantity": 6}],
    }, headers=H)
    return client.get(f"/events/{EV}/guests", headers=H).json()[-0:] and [x for x in client.get(f"/events/{EV}/guests", headers=H).json() if x["id"] == g["id"]][0]


def give_and_rsvp(holder, rec_name, date, qty=1):
    client.post(f"/public/rsvp/{holder['rsvp_token']}/distribute", json={"recipients": [{"name": rec_name, "email": f"{rec_name.lower().replace(' ', '')}@x.com", "visit_date": date, "quantity": qty}]})
    rec = [x for x in client.get(f"/events/{EV}/guests", headers=H).json() if x["name"] == rec_name][0]
    _rr = client.post(f"/public/rsvp/{rec['rsvp_token']}/respond", json={"attending": True})
    if _rr.status_code >= 400:
        print("respond failed:", _rr.status_code, _rr.text[:200])
    return [x for x in client.get(f"/events/{EV}/guests", headers=H).json() if x["name"] == rec_name][0]


# 1. holder chose Front / Sec A (family rep = base Thu pool); Friday recipient lands in the FRIDAY sibling, Sec A
h1 = add_holder("Bex Sponsor", seat_pool=front["id"], section="A")
r1 = give_and_rsvp(h1, "Rio Fri", D2)
check("recipient lands in the holder's pool, day-mapped, chosen section",
      r1["seating_category_id"] == fri_pool["id"] and r1["section_label"] == "A",
      str({"pool": r1["seating_category_id"][:8], "sec": r1["section_label"], "want": fri_pool["id"][:8]}))

# 2. fill Sec A on Friday (cap 2: Rio + one more), third overflows to Sec B of the SAME pool
r2 = give_and_rsvp(h1, "Ana Fri", D2)
r3 = give_and_rsvp(h1, "Cole Fri", D2)
check("full chosen section overflows within the holder's pool",
      r2["section_label"] == "A" and r3["seating_category_id"] == fri_pool["id"] and r3["section_label"] == "B",
      str({"r2": r2["section_label"], "r3_pool": r3["seating_category_id"][:8], "r3_sec": r3["section_label"]}))

# 3. pool exhausted on Friday (A2 + B2 all taken by r1..r3 + one more) -> next one falls back to type priorities (Back)
r4 = give_and_rsvp(h1, "Dee Fri", D2)
r5 = give_and_rsvp(h1, "Eve Fri", D2)
check("exhausted holder pool falls back to type priorities",
      r4["seating_category_id"] == fri_pool["id"] and r5["seating_category_id"] == back["id"],
      str({"r4": r4["section_label"], "r5_pool": r5["seating_category_id"][:8]}))

# 4. a holder with no seating choice: priorities as before
h2 = add_holder("Plain Holder")
r6 = give_and_rsvp(h2, "Finn Thu", D1)
check("holder without a choice keeps priority placement", r6["seating_category_id"] == back["id"], str(r6["seating_category_id"][:8]))

print()
if failures:
    print(f"FAILED: {len(failures)}")
    sys.exit(1)
print("test_allotment_seating: all clear")