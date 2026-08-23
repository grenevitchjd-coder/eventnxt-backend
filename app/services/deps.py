import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.services.events360_client import fetch_userinfo

# HTTPBearer (not OAuth2PasswordBearer) deliberately — EventNXT has no
# password login of its own, it only validates tokens Events360 already
# issued. This gives /docs a simple "paste your token" box instead of a
# username/password form that doesn't apply here.
bearer_scheme = HTTPBearer(auto_error=False)


class CurrentUser:
    """Plain identity object built from Events360's /oauth/userinfo response."""

    def __init__(self, data: dict):
        self.user_id = data["user_id"]
        self.organization_id = data["organization_id"]
        self.name = data["name"]
        self.email = data["email"]
        self.role = data["role"]


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> CurrentUser:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated.")
    try:
        data = fetch_userinfo(credentials.credentials)
    except httpx.HTTPStatusError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired session.")
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail="Could not reach Events360 to verify your session.")
    return CurrentUser(data)