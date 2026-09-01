"""eventnxt-backend: app/routers/ticket_types.py"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.order_item import OrderItem
from app.models.seating_category import SeatingCategory
from app.models.ticket_type import TicketType
from app.models.zone_section import ZoneSection
from app.services import seats as seats_service
from app.schemas.ticketing import TicketTypeAdminResponse, TicketTypeCreateOrUpdateRequest
from app.services.deps import CurrentUser
from app.services.event_access import require_event_access
from app.services.comp_tickets import event_days_for, get_event_settings_row
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


def _validate_type_date(db: Session, event_id: str, valid_date):
    """
    A dated type only makes sense when days sell individually; a whole-
    event type is illegal when the span says every ticket is for one day.
    Returns the normalized value.
    """
    row = get_event_settings_row(db, event_id)
    span = row.ticket_span if row else "single_day"
    clean = (valid_date or "").strip() or None
    if clean:
        if span not in ("per_day", "mixed"):
            raise HTTPException(
                status_code=400,
                detail="Day-specific ticket types need the event's span set to per-day or mixed (Event settings).",
            )
        days = event_days_for(db, event_id)
        if clean not in days:
            raise HTTPException(status_code=400, detail=f'"{clean}" isn\'t one of this event\'s days.')
    elif span == "per_day":
        raise HTTPException(
            status_code=400,
            detail="This event sells tickets per day — pick which day this type is for (or switch the span to mixed for whole-event passes).",
        )
    return clean


@router.post("/events/{event_id}/ticket-types", response_model=TicketTypeAdminResponse, status_code=201)
def create_ticket_type(
    event_id: str,
    payload: TicketTypeCreateOrUpdateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    valid_date = _validate_type_date(db, event_id, payload.valid_date)
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
        admits=payload.admits,
        valid_date=valid_date,
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

    # Omitted valid_date on update keeps the existing day (older clients
    # and the row editor don't always resend it); an empty string clears.
    incoming_date = payload.valid_date if payload.valid_date is not None else (ticket_type.valid_date or "")
    ticket_type.valid_date = _validate_type_date(db, event_id, incoming_date)
    ticket_type.name = payload.name
    ticket_type.description = payload.description
    ticket_type.price_cents = payload.price_cents
    ticket_type.quantity = payload.quantity
    ticket_type.max_per_order = payload.max_per_order
    ticket_type.admits = payload.admits
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

@router.post("/events/{event_id}/ticket-types/{ticket_type_id}/fan-out", response_model=list[TicketTypeAdminResponse])
def fan_out_ticket_type(
    event_id: str,
    ticket_type_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    """
    "Same every day": clone a DATED template type to every event day
    that doesn't already have a same-named dated type — same price,
    quantity, admits, and (when the template has one) a per-day clone of
    its seating pool with identical sections and freshly generated
    seats. Each day's inventory is fully independent: Saturday's seat 3
    selling never touches Sunday's. Idempotent — rerunning skips days
    already covered, so a half-failed fan-out just gets run again.
    Reserved-seat holds are NOT copied (a press hold is a per-day
    decision); block each day's seats in its own Seats view.
    """
    template = (
        db.query(TicketType)
        .filter(TicketType.id == ticket_type_id, TicketType.event_id == event_id)
        .first()
    )
    if not template:
        raise HTTPException(status_code=404, detail="Ticket type not found.")
    if not template.valid_date:
        raise HTTPException(status_code=400, detail="Fan-out starts from a day-specific type — give the template a day first.")
    days = event_days_for(db, event_id)
    if not days:
        raise HTTPException(status_code=400, detail="This event has no day list — set span and days in Event settings.")

    covered = {
        d
        for (d,) in db.query(TicketType.valid_date)
        .filter(TicketType.event_id == event_id, TicketType.name == template.name, TicketType.valid_date.isnot(None))
        .all()
    }
    template_pool = (
        db.query(SeatingCategory).filter(SeatingCategory.id == template.seating_category_id).first()
        if template.seating_category_id
        else None
    )
    template_sections = (
        db.query(ZoneSection)
        .filter(ZoneSection.seating_category_id == template_pool.id)
        .order_by(ZoneSection.sort_order)
        .all()
        if template_pool
        else []
    )

    created = []
    for day in days:
        if day in covered:
            continue
        pool_id = None
        if template_pool:
            mm_dd = f"{day[5:7]}/{day[8:10]}"
            pool = SeatingCategory(
                event_id=event_id,
                name=f"{template_pool.name} ({mm_dd})",
                capacity=template_pool.capacity,
                sales_grain=template_pool.sales_grain,
                row_label=template_pool.row_label,
                section_label=template_pool.section_label,
                table_count=template_pool.table_count,
                seats_per_table=template_pool.seats_per_table,
            )
            db.add(pool)
            db.flush()
            for i, sec in enumerate(template_sections):
                db.add(
                    ZoneSection(
                        seating_category_id=pool.id,
                        section_label=sec.section_label,
                        row_label=sec.row_label,
                        capacity=sec.capacity,
                        table_count=sec.table_count,
                        seats_per_table=sec.seats_per_table,
                        sort_order=i,
                    )
                )
            db.flush()
            seats_service.sync_seats_for_pool(db, pool)
            pool_id = pool.id
        clone = TicketType(
            event_id=event_id,
            organization_id=template.organization_id,
            seating_category_id=pool_id,
            name=template.name,
            description=template.description,
            price_cents=template.price_cents,
            quantity=template.quantity,
            max_per_order=template.max_per_order,
            admits=template.admits,
            valid_date=day,
            sales_start=template.sales_start,
            sales_end=template.sales_end,
            is_active=template.is_active,
            sort_order=template.sort_order,
        )
        db.add(clone)
        created.append(clone)
    db.commit()
    for c in created:
        db.refresh(c)
    return _with_counts(db, created) if created else []