from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.guest import Guest, GuestAllocationStatus
from app.models.guest_type import GuestType
from app.models.seating_category import SeatingCategory
from app.schemas.seating_category import (
    SeatingCategoryCreateRequest,
    SeatingCategoryUpdateRequest,
    SeatingCategoryResponse,
)
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


@router.patch("/{category_id}", response_model=SeatingCategoryResponse)
def update_seating_category(
    event_id: str,
    category_id: str,
    payload: SeatingCategoryUpdateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    category = (
        db.query(SeatingCategory)
        .filter(SeatingCategory.id == category_id, SeatingCategory.event_id == event_id)
        .with_for_update()
        .first()
    )
    if not category:
        raise HTTPException(status_code=404, detail="Seating category not found.")

    if payload.capacity < category.capacity:
        confirmed_count = (
            db.query(Guest)
            .filter(
                Guest.seating_category_id == category.id,
                Guest.allocation_status == GuestAllocationStatus.CONFIRMED,
            )
            .count()
        )
        if payload.capacity < confirmed_count:
            raise HTTPException(
                status_code=400,
                detail=f"Can't set capacity below {confirmed_count} — that many guests are already "
                f"confirmed in this category.",
            )

    category.name = payload.name
    category.capacity = payload.capacity
    db.commit()
    db.refresh(category)
    return category


@router.delete("/{category_id}", status_code=204)
def delete_seating_category(
    event_id: str,
    category_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    category = (
        db.query(SeatingCategory)
        .filter(SeatingCategory.id == category_id, SeatingCategory.event_id == event_id)
        .first()
    )
    if not category:
        raise HTTPException(status_code=404, detail="Seating category not found.")

    # Dependents become unassigned rather than blocking deletion — low-stakes,
    # easy to fix, unlike deleting a guest_type out from under a guest.
    db.query(Guest).filter(Guest.seating_category_id == category_id).update(
        {"seating_category_id": None}
    )
    db.query(GuestType).filter(GuestType.default_seating_category_id == category_id).update(
        {"default_seating_category_id": None}
    )

    db.delete(category)
    db.commit()