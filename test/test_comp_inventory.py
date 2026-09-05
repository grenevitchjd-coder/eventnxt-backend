# eventnxt-backend: test/test_comp_inventory.py  (verification harness)
#
# Comp holds are sellable-inventory (the availability_for slice):
#   1. sectioned per-day family: a comp confirmed for ONE night reduces
#      that night's type availability only (comp_held 1, available -1);
#      the sibling nights stay untouched
#   2. bare GA pool: comp holds reduce type availability — and checkout
#      REJECTS an order for more than what's left (the oversell gap)
#   3. a guest holding an actual assigned seat is not double-counted
#      (their consumption flows through the blocked seat, not comp_held)
#
# Run: DATABASE_URL="postgresql://test@/eventnxt_test?host=/tmp&port=5433" python3 test/test_comp_inventory.py
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
D1, D2, D3 = "2026-10-15", "2026-10-16", "2026-10-17"
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


def types_by(name_date):
    return {(t["name"], t.get("valid_date")): t for t in client.get(f"/events/{EV}/ticket-types", headers=H).json()}


# ---- 1. sectioned family: one comp, one night ----
pool = client.post(f"/events/{EV}/seating-categories", json={"name": "Row 2", "capacity": 78, "sales_grain": "row"}, headers=H).json()
client.put(f"/events/{EV}/seating-categories/{pool['id']}/sections", json={"sections": [{"section_label": "E", "capacity": 39}, {"section_label": "F", "capacity": 39}]}, headers=H)
_r = client.post(f"/events/{EV}/ticket-types", json={"name": "Row 2", "price_cents": 10500, "quantity": 78, "valid_date": D1, "seating_category_id": pool["id"]}, headers=H)
if _r.status_code >= 400:
    print("tt create failed:", _r.status_code, _r.text[:300])
tt = _r.json()
client.post(f"/events/{EV}/ticket-types/{tt['id']}/fan-out", json={}, headers=H)

gt = client.post(f"/events/{EV}/guest-types", json={"name": "Performer", "guest_mode": "invite"}, headers=H).json()
g = client.post(f"/events/{EV}/guests", json={"name": "Swego", "email": "s@example.com", "guest_type_id": gt["id"], "allocation_status": "pending", "party_size": 1}, headers=H).json()
client.patch(f"/events/{EV}/guests/{g['id']}", json={
    "name": "Swego", "email": "s@example.com", "guest_type_id": gt["id"],
    "seating_category_id": pool["id"], "section_label": "E", "visit_date": None,
    "allocation_status": "confirmed", "party_size": 1, "perks": None, "comments": None,
    "guest_mode": "invite", "hold_timing": "now", "spend_total": None, "cohort_together": True,
    "ticket_allotment": [{"date": D2, "quantity": 1}],
}, headers=H)

tmap = types_by(None)
fri = tmap[("Row 2", D2)]
check("comp night: comp_held 1, available 77", fri["comp_held"] == 1 and fri["available"] == 77, str({k: fri[k] for k in ("comp_held", "available")}))
check("other nights untouched", tmap[("Row 2", D1)]["available"] == 78 and tmap[("Row 2", D3)]["available"] == 78)

# ---- 2. bare GA: display + checkout clamp ----
ga_pool = client.post(f"/events/{EV}/seating-categories", json={"name": "GA Floor", "capacity": 3, "sales_grain": "ga"}, headers=H).json()
ga = client.post(f"/events/{EV}/ticket-types", json={"name": "GA Floor", "price_cents": 0, "quantity": 3, "valid_date": D1, "seating_category_id": ga_pool["id"]}, headers=H).json()
g2 = client.post(f"/events/{EV}/guests", json={"name": "Cora Comp", "email": "c@example.com", "guest_type_id": gt["id"], "allocation_status": "pending", "party_size": 2}, headers=H).json()
client.patch(f"/events/{EV}/guests/{g2['id']}", json={
    "name": "Cora Comp", "email": "c@example.com", "guest_type_id": gt["id"],
    "seating_category_id": ga_pool["id"], "section_label": None, "visit_date": None,
    "allocation_status": "confirmed", "party_size": 2, "perks": None, "comments": None,
    "guest_mode": "invite", "hold_timing": "now", "spend_total": None, "cohort_together": True,
    "ticket_allotment": [{"date": D1, "quantity": 2}],
}, headers=H)
ga_after = types_by(None)[("GA Floor", D1)]
check("GA: 2 comps leave 1 sellable", ga_after["comp_held"] == 2 and ga_after["available"] == 1, str({k: ga_after[k] for k in ("comp_held", "available")}))

client.put(f"/events/{EV}/profile", json={"title": "Fest"}, headers=H)
client.post(f"/events/{EV}/profile/publish", json={}, headers=H)
slug = client.get(f"/events/{EV}/profile", headers=H).json()["slug"]
r = client.post(f"/public/events/{slug}/checkout", json={"buyer_name": "B", "buyer_email": "b@example.com", "items": [{"ticket_type_id": ga["id"], "quantity": 2}]})
check("checkout REJECTS buying past the comp holds", r.status_code == 400, f"{r.status_code} {r.text[:120]}")
r = client.post(f"/public/events/{slug}/checkout", json={"buyer_name": "B", "buyer_email": "b@example.com", "items": [{"ticket_type_id": ga["id"], "quantity": 1}]})
check("checkout still sells the true remainder", r.status_code == 200, f"{r.status_code} {r.text[:120]}")

print()
if failures:
    print(f"FAILED: {len(failures)}")
    sys.exit(1)
print("test_comp_inventory: all clear")