from fastapi import APIRouter, Depends

from app.services.deps import get_current_user, CurrentUser

router = APIRouter(tags=["me"])


@router.get("/me")
def me(user: CurrentUser = Depends(get_current_user)):
    """
    Proves the whole auth loop works: if this returns real identity data,
    EventNXT successfully validated the token against Events360.
    """
    return {
        "user_id": user.user_id,
        "organization_id": user.organization_id,
        "name": user.name,
        "email": user.email,
        "role": user.role,
    }