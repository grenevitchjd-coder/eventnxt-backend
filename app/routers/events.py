import httpx
from fastapi import APIRouter, Depends, HTTPException

from app.config import settings
from app.services.deps import get_current_user, CurrentUser

router = APIRouter(prefix="/events", tags=["events"])


@router.get("")
def list_events(user: CurrentUser = Depends(get_current_user)):
    """
    Proxies Events360's GET /oauth/events — powers the real event picker
    in the frontend, replacing the old "paste an event ID" box.
    """
    response = httpx.get(
        f"{settings.events360_api_url}/oauth/events",
        headers={"Authorization": f"Bearer {user.raw_token}"},
        timeout=10.0,
    )
    if not response.is_success:
        raise HTTPException(status_code=502, detail="Could not reach Events360 to list events.")
    return response.json()