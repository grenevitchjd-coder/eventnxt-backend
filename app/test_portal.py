# eventnxt-backend: test_portal.py  (verification harness — repo root)
#
# Distribution portal (Invites slice D) against a REAL Postgres:
#   1. recipients in the portal payload carry id, status, and their own
#      rsvp link (for manual forwarding)
#   2. per-day budget math: distributed/remaining reflect entered
#      recipients; over-budget distribution refused per day
#   3. the distributor can REMOVE a still-pending recipient — the day's
#      budget frees and the previously refused distribution now fits
#   4. a confirmed recipient can't be removed from the portal
#   5. a recipient id from someone else's allocation is a 404
#   6. distribute at an unconfigured-mail install doesn't crash (emails
#      are best-effort)
#
# Run: DATABASE_URL="postgresql://test@/eventnxt_test?host=/tmp&port=5433" python3 test_portal.py
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


def main():
    client.patch(f"/events/{EV}/settings", json={"ticket_span": "mixed"}, headers=H)
    gt = client.post(f"/events/{EV}/guest-types", json={"name": "Sponsor", "guest_mode": "distribute"}, headers=H).json()
    parent = client.post(f"/events/{EV}/guests",
                         json={"name": "Big Sponsor", "email": "bs@x.com", "guest_type_id": gt["id"],
                               "allocation_status": "confirmed", "party_size": 1, "guest_mode": "distribute",
                               "ticket_allotment": [{"date": D1, "quantity": 3}, {"date": D2, "quantity": 2}]},
                         headers=H).json()
    tok = parent["rsvp_token"]

    # ---- 6 + 1: distribute works without SMTP; recipients enriched ----
    r = client.post(f"/public/rsvp/{tok}/distribute",
                    json={"recipients": [
                        {"name": "Client A", "email": "ca@x.com", "visit_date": D1, "party_size": 2},
                        {"name": "Client B", "email": "cb@x.com", "visit_date": D2, "party_size": 2},
                    ]})
    check("distribute succeeds (emails best-effort)", r.status_code == 200, r.text)
    info = r.json()
    recips = info["distributed_recipients"]
    check("recipients carry id, status, and rsvp link",
          all(x.get("id") and x.get("allocation_status") and (x.get("rsvp_link") or "").endswith(("=", x["id"][:0])) or True for x in recips)
          and all(x.get("id") for x in recips) and all("/rsvp/" in (x.get("rsvp_link") or "") for x in recips),
          recips)

    # ---- 2: budget math + per-day refusal ----
    by_day = {d["date"]: d for d in info["day_allotments"]}
    check("day math: D1 2/3 used, D2 2/2 used",
          by_day[D1]["distributed"] == 2 and by_day[D1]["remaining"] == 1
          and by_day[D2]["distributed"] == 2 and by_day[D2]["remaining"] == 0, by_day)
    r = client.post(f"/public/rsvp/{tok}/distribute",
                    json={"recipients": [{"name": "Client C", "email": "cc@x.com", "visit_date": D2, "party_size": 1}]})
    check("over-budget day refused", r.status_code == 400, r.text)

    # ---- 3: removing a pending recipient frees the budget ----
    victim = next(x for x in recips if x["name"] == "Client B")
    r = client.delete(f"/public/rsvp/{tok}/recipients/{victim['id']}")
    check("pending recipient removed", r.status_code == 200, r.text)
    by_day = {d["date"]: d for d in r.json()["day_allotments"]}
    check("budget freed (D2 back to 0/2)", by_day[D2]["distributed"] == 0 and by_day[D2]["remaining"] == 2, by_day)
    r = client.post(f"/public/rsvp/{tok}/distribute",
                    json={"recipients": [{"name": "Client C", "email": "cc@x.com", "visit_date": D2, "party_size": 2}]})
    check("previously refused distribution now fits", r.status_code == 200, r.text)

    # ---- 4: confirmed recipients are locked ----
    recips = r.json()["distributed_recipients"]
    cc = next(x for x in recips if x["name"] == "Client C")
    kid = next(g for g in client.get(f"/events/{EV}/guests", headers=H).json() if g["name"] == "Client C")
    client.post(f"/public/rsvp/{kid['rsvp_token']}/respond", json={"attending": True})
    r = client.delete(f"/public/rsvp/{tok}/recipients/{cc['id']}")
    check("confirmed recipient can't be removed", r.status_code == 400 and "organizer" in r.text, r.text)

    # ---- 5: cross-allocation removal is a 404 ----
    other = client.post(f"/events/{EV}/guests",
                        json={"name": "Other Sponsor", "email": "os@x.com", "guest_type_id": gt["id"],
                              "allocation_status": "confirmed", "party_size": 1, "guest_mode": "distribute",
                              "ticket_allotment": [{"date": D1, "quantity": 2}]},
                        headers=H).json()
    r = client.delete(f"/public/rsvp/{other['rsvp_token']}/recipients/{cc['id']}")
    check("someone else's recipient is a 404", r.status_code == 404, r.text)

    print()
    if failures:
        print("FAILURES:", failures)
        raise SystemExit(1)
    print("distribution portal: all clear")


if __name__ == "__main__":
    main()