from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.guest_type import GuestType
from app.schemas.guest_type import GuestTypeCreateRequest, GuestTypeResponse
from app.services.deps import get_current_user, CurrentUser

router = APIRouter(prefix="/guest-types", tags=["guest-types"])


@router.post("", response_model=GuestTypeResponse, status_code=201)
def create_guest_type(
    payload: GuestTypeCreateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Org-level, reusable across every event this org runs."""
    guest_type = GuestType(organization_id=user.organization_id, name=payload.name)
    db.add(guest_type)
    db.commit()
    db.refresh(guest_type)
    return guest_type


@router.get("", response_model=list[GuestTypeResponse])
def list_guest_types(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    return db.query(GuestType).filter(GuestType.organization_id == user.organization_id).all()