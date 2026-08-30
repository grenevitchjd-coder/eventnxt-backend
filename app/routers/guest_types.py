# eventnxt-backend: app/routers/guest_types.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.guest import Guest
from app.models.guest_type import GuestType
from app.models.guest_type_seating_priority import GuestTypeSeatingPriority
from app.models.guest_type_ticket_allotment import GuestTypeTicketAllotment
from app.models.seating_category import SeatingCategory
from app.schemas.guest_type import (
    GuestTypeCreateRequest,
    GuestTypeUpdateRequest,
    GuestTypeResponse,
    GuestTypeSeatingPriorityCreateRequest,
    GuestTypeSeatingPriorityResponse,
    TicketAllotmentDayResponse,
    TicketAllotmentDayUpsertRequest,
)
from app.services.deps import CurrentUser
from app.services.event_access import require_event_access

router = APIRouter(prefix="/events/{event_id}/guest-types", tags=["guest-types"])


def _get_guest_type_or_404(db: Session, event_id: str, guest_type_id: str) -> GuestType:
    guest_type = (
        db.query(GuestType).filter(GuestType.id == guest_type_id, GuestType.event_id == event_id).first()
    )
    if not guest_type:
        raise HTTPException(status_code=404, detail="Guest type not found.")
    return guest_type


@router.post("", response_model=GuestTypeResponse, status_code=201)
def create_guest_type(
    event_id: str,
    payload: GuestTypeCreateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    """
    Event-scoped — different events for the same org can define their own
    guest types, since who gets invited varies event to event. Seating
    preferences and the ticket allotment are both added separately — see
    the /seating-priorities and /ticket-allotments endpoints below.
    """
    guest_type = GuestType(event_id=event_id, name=payload.name, guest_mode=payload.guest_mode)
    db.add(guest_type)
    db.commit()
    db.refresh(guest_type)
    return guest_type


@router.get("", response_model=list[GuestTypeResponse])
def list_guest_types(
    event_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(require_event_access)
):
    return db.query(GuestType).filter(GuestType.event_id == event_id).all()


@router.patch("/{guest_type_id}", response_model=GuestTypeResponse)
def update_guest_type(
    event_id: str,
    guest_type_id: str,
    payload: GuestTypeUpdateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    guest_type = _get_guest_type_or_404(db, event_id, guest_type_id)
    guest_type.name = payload.name
    guest_type.guest_mode = payload.guest_mode
    db.commit()
    db.refresh(guest_type)
    return guest_type


@router.delete("/{guest_type_id}", status_code=204)
def delete_guest_type(
    event_id: str,
    guest_type_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    guest_type = _get_guest_type_or_404(db, event_id, guest_type_id)

    guest_count = db.query(Guest).filter(Guest.guest_type_id == guest_type_id).count()
    if guest_count > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Can't delete — {guest_count} guest(s) are still using this type. "
            f"Reassign or remove them first.",
        )

    db.query(GuestTypeSeatingPriority).filter(
        GuestTypeSeatingPriority.guest_type_id == guest_type_id
    ).delete()
    db.query(GuestTypeTicketAllotment).filter(
        GuestTypeTicketAllotment.guest_type_id == guest_type_id
    ).delete()
    db.delete(guest_type)
    db.commit()


# ---------- Ordered seating priority list ----------


@router.get("/{guest_type_id}/seating-priorities", response_model=list[GuestTypeSeatingPriorityResponse])
def list_seating_priorities(
    event_id: str,
    guest_type_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    _get_guest_type_or_404(db, event_id, guest_type_id)
    return (
        db.query(GuestTypeSeatingPriority)
        .filter(GuestTypeSeatingPriority.guest_type_id == guest_type_id)
        .order_by(GuestTypeSeatingPriority.priority_order)
        .all()
    )


@router.post(
    "/{guest_type_id}/seating-priorities",
    response_model=GuestTypeSeatingPriorityResponse,
    status_code=201,
)
def add_seating_priority(
    event_id: str,
    guest_type_id: str,
    payload: GuestTypeSeatingPriorityCreateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    """Always appends to the end of the list — see reordering note in the schema."""
    _get_guest_type_or_404(db, event_id, guest_type_id)

    category = (
        db.query(SeatingCategory)
        .filter(SeatingCategory.id == payload.seating_category_id, SeatingCategory.event_id == event_id)
        .first()
    )
    if not category:
        raise HTTPException(status_code=404, detail="That seating category doesn't belong to this event.")

    existing_count = (
        db.query(GuestTypeSeatingPriority)
        .filter(GuestTypeSeatingPriority.guest_type_id == guest_type_id)
        .count()
    )
    priority = GuestTypeSeatingPriority(
        guest_type_id=guest_type_id,
        seating_category_id=payload.seating_category_id,
        priority_order=existing_count,
    )
    db.add(priority)
    db.commit()
    db.refresh(priority)
    return priority


@router.delete("/{guest_type_id}/seating-priorities/{priority_id}", status_code=204)
def delete_seating_priority(
    event_id: str,
    guest_type_id: str,
    priority_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    _get_guest_type_or_404(db, event_id, guest_type_id)
    priority = (
        db.query(GuestTypeSeatingPriority)
        .filter(GuestTypeSeatingPriority.id == priority_id, GuestTypeSeatingPriority.guest_type_id == guest_type_id)
        .first()
    )
    if not priority:
        raise HTTPException(status_code=404, detail="Priority entry not found.")
    db.delete(priority)
    db.commit()


# ---------- Per-day ticket allotment (default for guests of this type) ----------


@router.get("/{guest_type_id}/ticket-allotments", response_model=list[TicketAllotmentDayResponse])
def list_ticket_allotments(
    event_id: str,
    guest_type_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    _get_guest_type_or_404(db, event_id, guest_type_id)
    return (
        db.query(GuestTypeTicketAllotment)
        .filter(GuestTypeTicketAllotment.guest_type_id == guest_type_id)
        .order_by(GuestTypeTicketAllotment.date)
        .all()
    )


@router.put("/{guest_type_id}/ticket-allotments/{date}", response_model=TicketAllotmentDayResponse)
def upsert_ticket_allotment_day(
    event_id: str,
    guest_type_id: str,
    date: str,
    payload: TicketAllotmentDayUpsertRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    """
    Set (or update) the ticket quantity for one specific day — "10 for
    Thursday" is its own separate pool from "5 for Saturday," so each day
    is addressed individually rather than resending the whole set.
    """
    _get_guest_type_or_404(db, event_id, guest_type_id)
    row = (
        db.query(GuestTypeTicketAllotment)
        .filter(GuestTypeTicketAllotment.guest_type_id == guest_type_id, GuestTypeTicketAllotment.date == date)
        .first()
    )
    if row:
        row.quantity = payload.quantity
    else:
        row = GuestTypeTicketAllotment(guest_type_id=guest_type_id, date=date, quantity=payload.quantity)
        db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{guest_type_id}/ticket-allotments/{date}", status_code=204)
def delete_ticket_allotment_day(
    event_id: str,
    guest_type_id: str,
    date: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    _get_guest_type_or_404(db, event_id, guest_type_id)
    row = (
        db.query(GuestTypeTicketAllotment)
        .filter(GuestTypeTicketAllotment.guest_type_id == guest_type_id, GuestTypeTicketAllotment.date == date)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="No allotment set for that date.")
    db.delete(row)
    db.commit()