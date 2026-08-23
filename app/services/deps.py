import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.services.events360_client import fetch_userinfo

# tokenUrl is informational only here (shown in /docs) — EventNXT never
# issues tokens itself, it only validates ones Events360 issued.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


class CurrentUser:
    """Plain identity object built from Events360's /oauth/userinfo response."""

    def __init__(self, data: dict):
        self.user_id = data["user_id"]
        self.organization_id = data["organization_id"]
        self.name = data["name"]
        self.email = data["email"]
        self.role = data["role"]


def get_current_user(token: str = Depends(oauth2_scheme)) -> CurrentUser:
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated.")
    try:
        data = fetch_userinfo(token)
    except httpx.HTTPStatusError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired session.")
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail="Could not reach Events360 to verify your session.")
    return CurrentUser(data)