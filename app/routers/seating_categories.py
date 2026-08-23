from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.seating_category import SeatingCategory
from app.schemas.seating_category import SeatingCategoryCreateRequest, SeatingCategoryResponse
from app.services.deps import CurrentUser
from app.services.event_access import require_event_access

router = APIRouter(prefix="/events/{event_id}/seating-categories", tags=["seating-categories"])


@router.post("", response_model=SeatingCategoryResponse, status_code=201)
def create_seating_category(
    event_id: str,
    payload: SeatingCategoryCreateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    category = SeatingCategory(event_id=event_id, name=payload.name, capacity=payload.capacity)
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


@router.get("", response_model=list[SeatingCategoryResponse])
def list_seating_categories(
    event_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    return db.query(SeatingCategory).filter(SeatingCategory.event_id == event_id).all()