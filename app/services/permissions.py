# eventnxt-backend/app/services/permissions.py
"""
Per-area permission gates, fed by Events360. The /oauth/userinfo payload
(fetched once per request by get_current_user, cached by FastAPI's
dependency cache) now carries the user's effective grants, computed by the
SAME function Events360's own enforcement uses — so the greyed sidebar the
frontend renders and the 403s raised here read one payload from one
computation, and can never drift.

Grant shape (from Events360):
    {"all": true}                                   # owner / org_admin
    {"all": false, "org_wide": [...],
     "by_event": {"<events360 event id>": [...]}}   # staff

Keys: "eventnxt.<area>.view" / "eventnxt.<area>.manage" for the five areas
(setup, guests, guest_list, promotion, money), plus the single
"eventnxt.checkin". Manage implies view — enforced here too, not just in
the role builder, so a manage-only role never gets a nonsense 403 on reads.

The action is derived from the HTTP method: reads (GET/HEAD/OPTIONS) need
view, everything else needs manage. require_area(...) accepts several
areas as ANY-OF — used where one route serves two pages (e.g. editing a
guest happens from both Invites and Guest list).

Rollout note: a userinfo payload with NO permissions field means Events360
hasn't deployed the permissions bridge yet — treated as all=True so
deploying this app first can't lock every staff member out. Events360
should still deploy first, per the standing backend-before-consumer rule.
"""

from fastapi import Depends, HTTPException, Request

from app.services.deps import CurrentUser
from app.services.event_access import require_event_access

READ_METHODS = {"GET", "HEAD", "OPTIONS"}

AREA_LABELS = {
    "setup": "Event setup",
    "guests": "Guests (Invites & Allotments)",
    "guest_list": "Guest list",
    "promotion": "Promotion",
    "money": "Money",
    "checkin": "Check-in",
}


def grants_for_event(user: CurrentUser, event_id: str):
    """The user's effective key set for this event, or None meaning
    'everything' (owner/org_admin, or pre-bridge Events360)."""
    perms = getattr(user, "permissions", None)
    if not perms or perms.get("all"):
        return None
    granted = set(perms.get("org_wide") or [])
    granted |= set((perms.get("by_event") or {}).get(str(event_id)) or [])
    return granted


def satisfies(granted, area: str, action: str) -> bool:
    if granted is None:
        return True
    if area == "checkin":
        return "eventnxt.checkin" in granted
    if action == "view":
        return f"eventnxt.{area}.view" in granted or f"eventnxt.{area}.manage" in granted
    return f"eventnxt.{area}.manage" in granted


def require_area(*areas: str):
    """Dependency factory: the request must hold view (reads) or manage
    (writes) for AT LEAST ONE of the given areas, scoped to the event in
    the path. Wraps require_event_access, so callers still get a
    CurrentUser with event_data attached — a drop-in replacement."""

    def dep(
        request: Request,
        event_id: str,
        user: CurrentUser = Depends(require_event_access),
    ) -> CurrentUser:
        action = "view" if request.method in READ_METHODS else "manage"
        granted = grants_for_event(user, event_id)
        if any(satisfies(granted, area, action) for area in areas):
            return user
        label = " or ".join(AREA_LABELS.get(a, a) for a in areas)
        raise HTTPException(
            status_code=403,
            detail=f"Your role doesn't include {label} ({action}) access for this event.",
        )

    return dep


require_setup = require_area("setup")
require_guests = require_area("guests")
require_guest_list = require_area("guest_list")
require_guests_or_guest_list = require_area("guests", "guest_list")
require_promotion = require_area("promotion")
require_money = require_area("money")
require_checkin = require_area("checkin")