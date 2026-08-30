"""eventnxt-backend: app/routers/ticket_types.py"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.order_item import OrderItem
from app.models.ticket_type import TicketType
from app.schemas.ticketing import TicketTypeAdminResponse, TicketTypeCreateOrUpdateRequest
from app.services.deps import CurrentUser
from app.services.event_access import require_event_access
from app.services.ticketing import availability_for

router = APIRouter(tags=["ticket-types"])


def _with_counts(db: Session, ticket_types: list[TicketType]) -> list[TicketTypeAdminResponse]:
    avail = availability_for(db, ticket_types) if ticket_types else {}
    out = []
    for t in ticket_types:
        resp = TicketTypeAdminResponse.model_validate(t)
        c = avail[t.id]
        resp.sold, resp.held, resp.available = c["sold"], c["held"], c["available"]
        out.append(resp)
    return out


@router.get("/events/{event_id}/ticket-types", response_model=list[TicketTypeAdminResponse])
def list_ticket_types(
    event_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(require_event_access)
):
    ticket_types = (
        db.query(TicketType)
        .filter(TicketType.event_id == event_id)
        .order_by(TicketType.sort_order, TicketType.created_at)
        .all()
    )
    return _with_counts(db, ticket_types)


@router.post("/events/{event_id}/ticket-types", response_model=TicketTypeAdminResponse, status_code=201)
def create_ticket_type(
    event_id: str,
    payload: TicketTypeCreateOrUpdateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    ticket_type = TicketType(
        event_id=event_id,
        # The org snapshot that unauthenticated checkout will copy onto
        # orders — from Events360's event payload, fetched by
        # require_event_access for this very request.
        organization_id=user.event_data["organization_id"],
        seating_category_id=payload.seating_category_id,
        name=payload.name,
        description=payload.description,
        price_cents=payload.price_cents,
        quantity=payload.quantity,
        max_per_order=payload.max_per_order,
        sales_start=payload.sales_start,
        sales_end=payload.sales_end,
        is_active=payload.is_active,
        sort_order=payload.sort_order,
    )
    db.add(ticket_type)
    db.commit()
    db.refresh(ticket_type)
    return _with_counts(db, [ticket_type])[0]


@router.put("/events/{event_id}/ticket-types/{ticket_type_id}", response_model=TicketTypeAdminResponse)
def update_ticket_type(
    event_id: str,
    ticket_type_id: str,
    payload: TicketTypeCreateOrUpdateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    ticket_type = (
        db.query(TicketType)
        .filter(TicketType.id == ticket_type_id, TicketType.event_id == event_id)
        .first()
    )
    if not ticket_type:
        raise HTTPException(status_code=404, detail="Ticket type not found.")

    ticket_type.name = payload.name
    ticket_type.description = payload.description
    ticket_type.price_cents = payload.price_cents
    ticket_type.quantity = payload.quantity
    ticket_type.max_per_order = payload.max_per_order
    ticket_type.seating_category_id = payload.seating_category_id
    ticket_type.sales_start = payload.sales_start
    ticket_type.sales_end = payload.sales_end
    ticket_type.is_active = payload.is_active
    ticket_type.sort_order = payload.sort_order
    db.commit()
    db.refresh(ticket_type)
    return _with_counts(db, [ticket_type])[0]


@router.delete("/events/{event_id}/ticket-types/{ticket_type_id}", status_code=204)
def delete_ticket_type(
    event_id: str,
    ticket_type_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    ticket_type = (
        db.query(TicketType)
        .filter(TicketType.id == ticket_type_id, TicketType.event_id == event_id)
        .first()
    )
    if not ticket_type:
        raise HTTPException(status_code=404, detail="Ticket type not found.")

    has_orders = db.query(OrderItem).filter(OrderItem.ticket_type_id == ticket_type.id).first()
    if has_orders:
        raise HTTPException(
            status_code=400,
            detail="This ticket type has orders against it — deactivate it instead of deleting, "
            "so the sales record stays intact.",
        )
    db.delete(ticket_type)
    db.commit()