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

Rollout note: a userinfo payload with NO permissions field AT ALL means
Events360 hasn't deployed the permissions bridge yet. While
`settings.permissions_bridge_required` is False (the default, for the
rollout window), that's treated as all=True so deploying this app first
can't lock every staff member out. Events360 should still deploy first,
per the standing backend-before-consumer rule — and once its permissions
bridge is confirmed live for every organization on EventNXT, flip
PERMISSIONS_BRIDGE_REQUIRED=true so a missing field can never again mean
"grant everything": at that point it's not a rollout gap, it's Events360
failing to tell us who someone is, and the safe answer is DENY, not allow.
Either way, every time the missing-field fallback actually fires, it's
logged — a fail-open default should never be silent.

Distinct from a MISSING field: a permissions object that's simply present
but empty (`{}`, or `{"all": false}` with no grants at all) is a real
answer from Events360 — "this user has zero grants" — and correctly
denies everything below. It is never treated as the pre-bridge case.
"""

import logging

from fastapi import Depends, HTTPException, Request

from app.config import settings
from app.services.deps import CurrentUser
from app.services.event_access import require_event_access

logger = logging.getLogger("eventnxt.permissions")

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
    'everything' (owner/org_admin, or — only during rollout — a
    pre-bridge Events360 payload missing the field entirely)."""
    perms = getattr(user, "permissions", None)
    if perms is None:
        # The field is ABSENT, not empty — Events360 hasn't sent it yet.
        if settings.permissions_bridge_required:
            logger.warning(
                "Permissions field missing for user %s on event %s; "
                "PERMISSIONS_BRIDGE_REQUIRED is set, denying instead of "
                "falling back to all-access.",
                getattr(user, "user_id", "?"), event_id,
            )
            return set()
        logger.warning(
            "Permissions field missing for user %s on event %s; falling "
            "back to all-access (pre-bridge rollout default — set "
            "PERMISSIONS_BRIDGE_REQUIRED=true once Events360's bridge is "
            "confirmed live).",
            getattr(user, "user_id", "?"), event_id,
        )
        return None
    if perms.get("all"):
        return None  # an explicit owner/org_admin grant, not a fallback
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