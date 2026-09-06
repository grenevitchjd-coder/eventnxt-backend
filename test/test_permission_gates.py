# eventnxt-backend/test/test_permission_gates.py
"""
Pins the per-area permission gates (the Events360 roles bridge).

Technique: fetch_userinfo and the event-access lookup are monkeypatched, so
no live Events360 (or database) is needed — a 403 is raised by the gate
BEFORE any db work, and "allowed" is asserted as status != 401/403 (the
request may then succeed or fail on data, which is not this suite's
concern). Run: python3 test/test_permission_gates.py (DATABASE_URL must be
set but is never connected to for the denial paths).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("DATABASE_URL", "postgresql://localhost/eventnxt_unused")

from unittest.mock import patch

from fastapi.testclient import TestClient

import app.services.deps as deps_module
import app.services.event_access as event_access_module
from app.main import app

EV = "11111111-1111-1111-1111-111111111111"
OTHER_EV = "22222222-2222-2222-2222-222222222222"


def make_userinfo(perms):
    return {
        "user_id": "u1", "organization_id": "o1", "name": "Probe", "email": "p@t.co",
        "role": "staff", "permissions": perms,
    }


class FakeEventResp:
    status_code = 200
    def raise_for_status(self): pass
    def json(self): return {"id": EV, "organization_id": "o1", "name": "E", "status": "active"}


def client_with(perms):
    c = TestClient(app)
    c.headers["Authorization"] = "Bearer fake"
    patches = [
        patch.object(deps_module, "fetch_userinfo", lambda tok: make_userinfo(perms)),
        patch.object(event_access_module.httpx, "get", lambda *a, **k: FakeEventResp()),
    ]
    for p in patches:
        p.start()
    c._patches = patches
    return c


def done(c):
    for p in c._patches:
        p.stop()


def check(c, method, path, expect_denied, label):
    kwargs = {"json": {}} if method in ("post", "patch", "put") else {}
    r = getattr(c, method)(path, **kwargs)
    denied = r.status_code == 403
    assert r.status_code != 401, f"{label}: unexpected 401"
    assert denied == expect_denied, f"{label}: status={r.status_code}, expected {'403' if expect_denied else 'not 403'} — {r.text[:120]}"


def run():
    # 1. Door staff (checkin + guest_list.view, org-wide): roster+stats yes, everything else no
    c = client_with({"all": False, "org_wide": ["eventnxt.checkin", "eventnxt.guest_list.view"], "by_event": {}})
    check(c, "get", f"/events/{EV}/guests/roster/door", False, "door roster with guest_list.view")
    check(c, "get", f"/events/{EV}/check-in/stats", False, "checkin stats with checkin")
    check(c, "post", f"/events/{EV}/check-in/SOMECODE", False, "check-in POST with checkin (single key covers writes)")
    check(c, "get", f"/events/{EV}/guests", False, "guest list read via guest_list.view (any-of)")
    check(c, "post", f"/events/{EV}/guests", True, "add guest denied")
    check(c, "patch", f"/events/{EV}/guests/g1", True, "edit guest denied (view only)")
    check(c, "get", f"/events/{EV}/ticket-types", True, "setup read denied")
    check(c, "get", f"/events/{EV}/promo-stats", True, "money read denied")
    check(c, "get", f"/events/{EV}/promo-codes", True, "promotion read denied")
    check(c, "get", f"/events/{EV}/orders", True, "orders denied")
    done(c)
    print("1. door staff profile: PASS")

    # 2. Manage implies view server-side: guests.manage alone can READ guests
    c = client_with({"all": False, "org_wide": ["eventnxt.guests.manage"], "by_event": {}})
    check(c, "get", f"/events/{EV}/guests", False, "guests read via manage-implies-view")
    check(c, "post", f"/events/{EV}/guests/send-invites", False, "guests write with manage")
    check(c, "get", f"/events/{EV}/guests/roster/door", True, "roster still needs guest_list")
    done(c)
    print("2. manage implies view + guest_list stays separate: PASS")

    # 3. Event scoping: grant on EV does not open OTHER_EV
    c = client_with({"all": False, "org_wide": [], "by_event": {EV: ["eventnxt.setup.manage", "eventnxt.setup.view"]}})
    check(c, "get", f"/events/{EV}/ticket-types", False, "scoped setup read on granted event")
    check(c, "patch", f"/events/{EV}/settings", False, "scoped setup write on granted event")
    check(c, "get", f"/events/{OTHER_EV}/ticket-types", True, "denied on other event")
    done(c)
    print("3. event scoping (no leak across events): PASS")

    # 4. Promotion vs money split inside sales.py
    c = client_with({"all": False, "org_wide": ["eventnxt.promotion.manage", "eventnxt.promotion.view"], "by_event": {}})
    check(c, "get", f"/events/{EV}/promo-codes", False, "promoter reads codes")
    check(c, "post", f"/events/{EV}/promo-codes", False, "promoter creates codes")
    check(c, "get", f"/events/{EV}/promo-stats", True, "promoter denied $-per-code")
    check(c, "get", f"/events/{EV}/sales", True, "promoter denied raw sales")
    check(c, "get", f"/events/{EV}/reward-redemptions", True, "promoter denied payouts")
    done(c)
    c = client_with({"all": False, "org_wide": ["eventnxt.money.view"], "by_event": {}})
    check(c, "get", f"/events/{EV}/promo-stats", False, "money.view reads promo-stats")
    check(c, "patch", f"/events/{EV}/reward-redemptions/r1/mark-paid", True, "money.view cannot mark-paid")
    check(c, "post", f"/events/{EV}/orders/o1/refund", True, "money.view cannot refund")
    done(c)
    print("4. promotion/money split + view-vs-manage on money: PASS")

    # 5. all=True and MISSING permissions field both open everything (rollout safety)
    for perms, label in [({"all": True, "org_wide": [], "by_event": {}}, "all=True"), (None, "pre-bridge payload")]:
        c = client_with(perms)
        check(c, "get", f"/events/{EV}/promo-stats", False, f"{label}: money read")
        check(c, "post", f"/events/{EV}/guests", False, f"{label}: guests write")
        done(c)
    print("5. owner + pre-bridge fallback open: PASS")

    # 6. Public routes untouched by the sweep: no auth, no 401/403 from gates
    c = TestClient(app)
    r = c.get("/public/events/some-slug")
    assert r.status_code not in (401, 403), f"public event page gated: {r.status_code}"
    print("6. public routes ungated: PASS")

    print("\nALL PERMISSION-GATE PINS PASS")


if __name__ == "__main__":
    run()