# eventnxt-backend: test/test_section_summary.py  (verification harness)
#
# The per-section availability decomposition (Seating summary page):
#   1. sectioned pool: a comp placed in one section shows as `given`
#      there and `left` drops by the party — other sections untouched
#   2. `left` per section equals what comp placement actually enforces
#      (place until full; the enforced overflow matches the display)
#   3. imported (CSV) sales land as `bought` on a sectionless pool and
#      the pool line's confirmed_avail now subtracts box office (the
#      capacity - committed - box_office fix)
#   4. sectionless pools work as recipient-seating targets (the
#      missing-label path used to report 0 room forever)
#
# Run: DATABASE_URL="postgresql://test@/eventnxt_test?host=/tmp&port=5433" python3 test/test_section_summary.py
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
D1, D2, D3 = "2026-11-05", "2026-11-06", "2026-11-07"
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


def section_map(cat_id):
    pools = client.get(f"/events/{EV}/seating-categories/section-summary", headers=H).json()
    pool = next(p for p in pools if p["category_id"] == cat_id)
    return {s["section_label"]: s for s in pool["sections"]}


# ---- 1+2: sectioned pool — given/left per section; display == enforcement ----
pool = client.post(f"/events/{EV}/seating-categories", json={"name": "Row 1", "capacity": 5, "sales_grain": "row"}, headers=H).json()
client.put(f"/events/{EV}/seating-categories/{pool['id']}/sections", json={"sections": [{"section_label": "C", "capacity": 2}, {"section_label": "D", "capacity": 3}]}, headers=H)

gt = client.post(f"/events/{EV}/guest-types", json={"name": "Sponsor", "guest_mode": "distribute"}, headers=H).json()
h = client.post(f"/events/{EV}/guests", json={"name": "Holder", "email": "h@example.com", "guest_type_id": gt["id"], "allocation_status": "confirmed", "party_size": 1, "guest_mode": "distribute"}, headers=H).json()
client.patch(f"/events/{EV}/guests/{h['id']}", json={
    "name": "Holder", "email": "h@example.com", "guest_type_id": gt["id"],
    "seating_category_id": None, "section_label": None, "visit_date": None,
    "allocation_status": "confirmed", "party_size": 1, "perks": None, "comments": None,
    "guest_mode": "distribute", "hold_timing": "now", "spend_total": None, "cohort_together": True,
    "recipient_seating_category_id": pool["id"], "recipient_section_label": "C",
    "ticket_allotment": [{"date": D1, "quantity": 6}],
}, headers=H)
h = [x for x in client.get(f"/events/{EV}/guests", headers=H).json() if x["id"] == h["id"]][0]

client.post(f"/public/rsvp/{h['rsvp_token']}/distribute", json={"recipients": [{"name": "R One", "email": "r1@example.com", "visit_date": D1, "party_size": 1}]})
secs = section_map(pool["id"])
check("comp shows as given in its section, left drops", secs["C"]["given"] == 1 and secs["C"]["left"] == 1, str(secs["C"]))
check("other section untouched", secs["D"]["given"] == 0 and secs["D"]["left"] == 3, str(secs["D"]))

# fill C (cap 2), next recipients must land in D exactly when the display says C is full
client.post(f"/public/rsvp/{h['rsvp_token']}/distribute", json={"recipients": [
    {"name": "R Two", "email": "r2@example.com", "visit_date": D1, "party_size": 1},
    {"name": "R Three", "email": "r3@example.com", "visit_date": D1, "party_size": 1},
]})
secs = section_map(pool["id"])
r3 = next(g for g in client.get(f"/events/{EV}/guests", headers=H).json() if g["name"] == "R Three")
check("display and enforcement agree: C full -> overflow to D",
      secs["C"]["left"] == 0 and r3["section_label"] == "D" and secs["D"]["given"] == 1,
      str({"C": secs["C"], "r3": r3["section_label"], "D": secs["D"]}))

# ---- 3: CSV sales = bought; confirmed_avail subtracts box office ----
ga = client.post(f"/events/{EV}/seating-categories", json={"name": "GA Floor", "capacity": 10, "sales_grain": "ga"}, headers=H).json()
client.post(f"/events/{EV}/sales/import", json={"rows": [{"ticket_type": "GA Floor", "quantity": 4, "buyer_email": "b@example.com"}]}, headers=H)
secs = section_map(ga["id"])
check("sectionless pool renders one row with imported sales as bought",
      list(secs.keys()) == [None] and secs[None]["bought"] == 4 and secs[None]["left"] == 6, str(secs))
summary = {r["category_name"]: r for r in client.get(f"/events/{EV}/seating-categories/summary", headers=H).json()}
check("confirmed_avail subtracts box office (capacity 10 - 0 committed - 4 sold = 6)",
      summary["GA Floor"]["confirmed_avail"] == 6 and summary["GA Floor"]["estimated_avail"] == 6,
      str({k: summary["GA Floor"][k] for k in ("confirmed_avail", "estimated_avail", "box_office")}))

# ---- 4: sectionless pool as a recipient-seating target ----
client.patch(f"/events/{EV}/guests/{h['id']}", json={
    "name": "Holder", "email": "h@example.com", "guest_type_id": gt["id"],
    "seating_category_id": None, "section_label": None, "visit_date": None,
    "allocation_status": "confirmed", "party_size": 1, "perks": None, "comments": None,
    "guest_mode": "distribute", "hold_timing": "now", "spend_total": None, "cohort_together": True,
    "recipient_seating_category_id": ga["id"], "recipient_section_label": None,
}, headers=H)
client.post(f"/public/rsvp/{h['rsvp_token']}/distribute", json={"recipients": [{"name": "R Four", "email": "r4@example.com", "visit_date": D1, "party_size": 2}]})
r4 = next(g for g in client.get(f"/events/{EV}/guests", headers=H).json() if g["name"] == "R Four")
secs = section_map(ga["id"])
check("recipient lands in the sectionless pool (missing-label path fixed)",
      r4["seating_category_id"] == ga["id"] and r4["allocation_status"] == "confirmed", str(r4["seating_category_id"]))
check("their heads count as given against the pool row", secs[None]["given"] == 2 and secs[None]["left"] == 4, str(secs[None]))

print()
if failures:
    print(f"FAILED: {len(failures)}")
    sys.exit(1)
print("test_section_summary: all clear")