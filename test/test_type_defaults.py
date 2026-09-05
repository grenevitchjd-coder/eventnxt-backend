# eventnxt-backend: test/test_type_defaults.py  (verification harness)
#
# Type-level defaults fully configure an offer (0040 slice):
#   1. a type with per-day allotment rows AND default_spend_total —
#      adding a guest stamps spend_total from the type (the "Volunteer:
#      2 across Thu 2 / Fri 2" recipe configured ONCE on the type)
#   2. explicit payload spend_total wins over the type default
#   3. a type with no default leaves spend_total null (fixed offer)
#   4. the inherited total actually produces the chooser: the public
#      RSVP payload flags choose_within_caps with the right numbers
#   5. shape defaults (day_scope 'choose' + count) still work alongside
#
# Run: DATABASE_URL="postgresql://test@/eventnxt_test?host=/tmp&port=5433" python3 test/test_type_defaults.py
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

EV = str(uuid.uuid4())
ORG_ID = str(uuid.uuid4())
D1, D2 = "2026-11-06", "2026-11-07"
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
    event_data = {"organization_id": ORG_ID, "name": "Fest", "start_date": D1, "end_date": D2}


app.dependency_overrides[get_current_user] = lambda: FakeUser()
app.dependency_overrides[require_event_access] = lambda event_id: FakeUser()
client = TestClient(app)
H = {"Authorization": "Bearer tok"}

client.patch(f"/events/{EV}/settings", json={"ticket_span": "per_day", "ticketing_mode": "native", "comp_delivery": "rsvp_required"}, headers=H)

# --- 1+4: per-day rows + default total on the type -> added guest gets the chooser
r = client.post(f"/events/{EV}/guest-types", json={"name": "Volunteer", "guest_mode": "invite", "default_spend_total": 2}, headers=H)
check("type created with default_spend_total", r.status_code == 201 and r.json().get("default_spend_total") == 2, r.text[:200])
vol = r.json()
for d in (D1, D2):
    client.put(f"/events/{EV}/guest-types/{vol['id']}/ticket-allotments/{d}", json={"quantity": 2}, headers=H)

r = client.post(f"/events/{EV}/guests", json={"name": "Vera Volunteer", "email": "vera@example.com", "guest_type_id": vol["id"], "allocation_status": "pending", "party_size": 1}, headers=H)
check("guest inherits type default_spend_total", r.status_code == 201 and r.json().get("spend_total") == 2, r.text[:200])
vera = r.json()

r = client.get(f"/public/rsvp/{vera['rsvp_token']}")
info = r.json() if r.status_code == 200 else {}
check("inherited total produces the chooser", info.get("choose_within_caps") is True and info.get("spend_total") == 2, str(info)[:200])

# --- 2: explicit payload wins over the type default
r = client.post(f"/events/{EV}/guests", json={"name": "Eli Explicit", "email": "eli@example.com", "guest_type_id": vol["id"], "allocation_status": "pending", "party_size": 1, "spend_total": 3}, headers=H)
check("explicit spend_total beats the type default", r.status_code == 201 and r.json().get("spend_total") == 3, r.text[:200])

# --- 3: no default -> null (fixed offer)
r = client.post(f"/events/{EV}/guest-types", json={"name": "Press", "guest_mode": "invite"}, headers=H)
press = r.json()
r = client.post(f"/events/{EV}/guests", json={"name": "Pat Press", "email": "pat@example.com", "guest_type_id": press["id"], "allocation_status": "pending", "party_size": 1}, headers=H)
check("no type default leaves spend_total null", r.status_code == 201 and r.json().get("spend_total") is None, r.text[:200])

# --- 5: shape defaults still coexist (choose scope + count, no explicit rows)
r = client.post(f"/events/{EV}/guest-types", json={"name": "Model", "guest_mode": "invite", "day_scope": "choose", "default_ticket_count": 2, "default_spend_total": 2}, headers=H)
model = r.json()
r = client.post(f"/events/{EV}/guests", json={"name": "Mia Model", "email": "mia@example.com", "guest_type_id": model["id"], "allocation_status": "pending", "party_size": 1}, headers=H)
mia = r.json()
r = client.get(f"/public/rsvp/{mia['rsvp_token']}")
info = r.json() if r.status_code == 200 else {}
check("shape-default type + default total also choosers", info.get("choose_within_caps") is True and info.get("spend_total") == 2, str(info)[:200])

# --- update round-trip: the field survives a type edit
r = client.patch(f"/events/{EV}/guest-types/{vol['id']}", json={"name": "Volunteer", "guest_mode": "invite", "default_spend_total": 4}, headers=H)
check("type update round-trips default_spend_total", r.status_code == 200 and r.json().get("default_spend_total") == 4, r.text[:200])

print()
if failures:
    print(f"FAILED: {len(failures)}")
    sys.exit(1)
print("test_type_defaults: all clear")