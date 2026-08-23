"""
Verifies an event_id is real and belongs to the caller's own organization
before any EventNXT endpoint acts on it — calls Events360's
GET /oauth/events/{event_id}, since EventNXT has no local copy of the
Event record (Events360 owns it) and no direct database link between the
two services.
"""

import httpx
from fastapi import Depends, HTTPException

from app.config import settings
from app.services.deps import get_current_user, CurrentUser


def require_event_access(event_id: str, user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    response = httpx.get(
        f"{settings.events360_api_url}/oauth/events/{event_id}",
        headers={"Authorization": f"Bearer {user.raw_token}"},
        timeout=10.0,
    )
    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="Event not found, or you don't have access to it.")
    response.raise_for_status()
    return user