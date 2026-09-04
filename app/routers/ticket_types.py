"""eventnxt-backend: app/routers/ticket_types.py"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.order_item import OrderItem
from pydantic import BaseModel, Field
from typing import Optional

from app.models.pass_member import PassMember
from app.models.seating_category import SeatingCategory
from app.models.ticket_type import TicketType
from app.models.zone_section import ZoneSection
from app.services import seats as seats_service
from app.schemas.ticketing import TicketTypeAdminResponse, TicketTypeCreateOrUpdateRequest
from app.services.deps import CurrentUser
from app.services.event_access import require_event_access
from app.services.comp_tickets import event_days_for, get_event_settings_row
from app.services.seating import name_family_filter, normalized_name
from app.services.ticketing import availability_for

router = APIRouter(tags=["ticket-types"])


def _with_counts(db: Session, ticket_types: list[TicketType]) -> list[TicketTypeAdminResponse]:
    avail = availability_for(db, ticket_types) if ticket_types else {}
    out = []
    pass_ids = {
        pid for (pid,) in db.query(PassMember.pass_type_id)
        .filter(PassMember.pass_type_id.in_([t.id for t in ticket_types]))
        .all()
    } if ticket_types else set()
    for t in ticket_types:
        resp = TicketTypeAdminResponse.model_validate(t)
        c = avail[t.id]
        resp.sold, resp.held, resp.available = c["sold"], c["held"], c["available"]
        resp.is_pass = t.id in pass_ids
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
    # Capacity-drift fix: a bare GA pool born alongside this type (the
    # composer sets pool capacity = type quantity) should FOLLOW the
    # type when its quantity is edited — otherwise raising 10 → 14 shows
    # 14 for sale while checkout still refuses past the pool's stale 10.
    # Only when it's unambiguous: 'ga' grain (seats are physical chairs
    # and table capacity is derived — those never auto-resize), no
    # sections (sectioned zones are managed per-section), and no OTHER
    # type selling the same pool (a shared pool's capacity is a
    # deliberate shared budget, not one type's mirror).
    if ticket_type.seating_category_id and payload.quantity is not None:
        from app.models.zone_section import ZoneSection

        pool = (
            db.query(SeatingCategory)
            .filter(SeatingCategory.id == ticket_type.seating_category_id, SeatingCategory.event_id == event_id)
            .first()
        )
        if pool and pool.sales_grain == "ga":
            has_sections = (
                db.query(ZoneSection.id).filter(ZoneSection.seating_category_id == pool.id).first() is not None
            )
            sibling = (
                db.query(TicketType.id)
                .filter(
                    TicketType.seating_category_id == pool.id,
                    TicketType.id != ticket_type.id,
                )
                .first()
                is not None
            )
            if not has_sections and not sibling and pool.capacity != payload.quantity:
                pool.capacity = payload.quantity
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
    if db.query(PassMember.id).filter(PassMember.member_type_id == ticket_type_id).first():
        raise HTTPException(status_code=400, detail="This night is part of an all-days pass — delete the pass first.")
    db.query(PassMember).filter(PassMember.pass_type_id == ticket_type_id).delete()

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
        .filter(TicketType.event_id == event_id, name_family_filter(template.name), TicketType.valid_date.isnot(None))
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

def _seated_pass_family(db: Session, event_id: str, template: TicketType) -> list[TicketType]:
    """
    The dated family a pass can ride on, resolved from a nightly
    template by NORMALIZED name (trim / collapse whitespace / lowercase —
    a stray space can't split a family). Raises buyer-readable 400s when
    the family isn't pass-shaped: fewer than two distinct days, mixed or
    table selling grains, or an active pass already covering a member.
    """
    family = (
        db.query(TicketType)
        .filter(TicketType.event_id == event_id, name_family_filter(template.name), TicketType.valid_date.isnot(None))
        .order_by(TicketType.valid_date)
        .all()
    )
    if len({m.valid_date for m in family}) < 2:
        raise HTTPException(status_code=400, detail=f'"{template.name}" only exists for one day — fan it out first.')
    pools = (
        db.query(SeatingCategory).filter(SeatingCategory.id.in_([m.seating_category_id for m in family if m.seating_category_id])).all()
    )
    grains = {p.sales_grain for p in pools}
    if len(pools) == len(family) and grains <= {"seat"}:
        pass  # seat family: the pass claims one chair on every night
    elif len(pools) == len(family) and grains <= {"row"}:
        pass  # sectioned family: the pass claims one section head per night
    elif grains <= {"ga"}:
        pass  # GA family (pools optional): the pass consumes plain nightly counts
    else:
        raise HTTPException(
            status_code=400,
            detail="All-days passes need every night selling the same way — all assigned seats, all rows, or all GA. (Table families aren't pass-able yet.)",
        )
    already = (
        db.query(PassMember.id)
        .join(TicketType, TicketType.id == PassMember.pass_type_id)
        .filter(PassMember.member_type_id.in_([m.id for m in family]), TicketType.is_active.is_(True))
        .first()
    )
    if already:
        raise HTTPException(status_code=400, detail=f'"{template.name}" already has an active all-days pass.')
    return family


class PassCreateRequest(BaseModel):
    name: str
    price_cents: int = Field(ge=0)
    quantity: int = Field(ge=1)  # the pass's own cap — how many packages to sell
    max_per_order: int = Field(default=4, ge=1)


@router.post("/events/{event_id}/ticket-types/{ticket_type_id}/pass", response_model=TicketTypeAdminResponse, status_code=201)
def create_pass_from_type(
    event_id: str,
    ticket_type_id: str,
    payload: PassCreateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    """
    Derived all-days pass: a whole-event type linked to every night of a
    nightly family (same-named dated types — the fan-out shape). It owns
    no inventory of its own: a seat family claims the same chair every
    night, a sectioned row family claims one section head per night (the
    buyer picks a section for each night), and a GA family consumes one
    head of each night's plain count. Availability is derived — the
    thinnest night is the ceiling — plus the pass's own quantity cap.
    """
    row = get_event_settings_row(db, event_id)
    if not row or row.ticket_span != "mixed":
        raise HTTPException(status_code=400, detail="All-days passes need the event span set to mixed (Event settings).")
    template = (
        db.query(TicketType)
        .filter(TicketType.id == ticket_type_id, TicketType.event_id == event_id)
        .first()
    )
    if not template or not template.valid_date:
        raise HTTPException(status_code=404, detail="Pass templates are day-specific ticket types.")
    family = _seated_pass_family(db, event_id, template)

    pass_type = TicketType(
        event_id=event_id,
        organization_id=template.organization_id,
        seating_category_id=None,
        name=payload.name.strip() or f"{template.name} — All Days",
        description=None,
        price_cents=payload.price_cents,
        quantity=payload.quantity,
        max_per_order=payload.max_per_order,
        admits=1,
        valid_date=None,
        sales_start=None,
        sales_end=None,
        is_active=True,
        sort_order=template.sort_order,
    )
    db.add(pass_type)
    db.flush()
    for m in family:
        db.add(PassMember(pass_type_id=pass_type.id, member_type_id=m.id))
    db.commit()
    db.refresh(pass_type)
    return _with_counts(db, [pass_type])[0]

class ConvertToPassRequest(BaseModel):
    template_type_id: str  # any nightly member of the family to link to


@router.post("/events/{event_id}/ticket-types/{ticket_type_id}/convert-to-pass", response_model=TicketTypeAdminResponse)
def convert_type_to_pass(
    event_id: str,
    ticket_type_id: str,
    payload: ConvertToPassRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    """
    Retro-fit for "the all-days package was made first": take a
    STANDALONE undated type (own pool, own duplicate seats) and rewire it
    into a derived pass on an existing nightly seated family — same
    physical chairs as the nights, no second inventory. Keeps the type's
    name / price / quantity-cap / max-per-order; discards its own pool,
    sections, and seats (they were duplicates of the real room).

    Refused whenever throwing the pool away would lose truth: anything
    sold or held on the type, seats blocked or assigned to guests, guests
    or seating priorities pointing at the pool, or the pool shared by
    another ticket type (then we only detach, never delete). The family
    itself is validated exactly like the All-days-pass button
    (normalized-name match, 2+ days, all assigned-seat, no active pass).
    """
    from app.models.guest import Guest
    from app.models.guest_type_seating_priority import GuestTypeSeatingPriority
    from app.models.seat import Seat
    from app.models.ticket import Ticket

    row = get_event_settings_row(db, event_id)
    if not row or row.ticket_span != "mixed":
        raise HTTPException(status_code=400, detail="All-days passes need the event span set to mixed (Event settings).")

    ticket_type = (
        db.query(TicketType)
        .filter(TicketType.id == ticket_type_id, TicketType.event_id == event_id)
        .first()
    )
    if not ticket_type:
        raise HTTPException(status_code=404, detail="Ticket type not found.")
    if ticket_type.valid_date:
        raise HTTPException(status_code=400, detail="Only whole-event (undated) types can become passes — this one is day-specific.")
    if db.query(PassMember.id).filter(PassMember.pass_type_id == ticket_type.id).first():
        raise HTTPException(status_code=400, detail="This type is already an all-days pass.")
    if db.query(PassMember.id).filter(PassMember.member_type_id == ticket_type.id).first():
        raise HTTPException(status_code=400, detail="This type is a night inside an existing pass — it can't become one.")

    # Nothing may have been sold or held against the standalone identity:
    # its codes and holds point at seats that are about to stop existing.
    if db.query(Ticket.id).filter(Ticket.ticket_type_id == ticket_type.id).first():
        raise HTTPException(status_code=400, detail="Tickets have already been issued on this type — it can't be converted. Deactivate it and use the All-days pass button instead.")
    if db.query(OrderItem.id).filter(OrderItem.ticket_type_id == ticket_type.id).first():
        raise HTTPException(status_code=400, detail="This type has orders (including pending holds) — it can't be converted. Deactivate it and use the All-days pass button instead.")

    template = (
        db.query(TicketType)
        .filter(TicketType.id == payload.template_type_id, TicketType.event_id == event_id)
        .first()
    )
    if not template or not template.valid_date:
        raise HTTPException(status_code=404, detail="Pick a day-specific nightly type to link to.")
    family = _seated_pass_family(db, event_id, template)

    pool = (
        db.query(SeatingCategory).filter(SeatingCategory.id == ticket_type.seating_category_id).first()
        if ticket_type.seating_category_id
        else None
    )
    if pool:
        if db.query(Guest.id).filter(Guest.seating_category_id == pool.id).first():
            raise HTTPException(status_code=400, detail=f'Guests are placed in "{pool.name}" — move them to the nightly pools first.')
        if db.query(GuestTypeSeatingPriority.id).filter(GuestTypeSeatingPriority.seating_category_id == pool.id).first():
            raise HTTPException(status_code=400, detail=f'Guest-type seating priorities target "{pool.name}" — repoint them at the nightly pools first.')
        blocked = (
            db.query(Seat.id)
            .filter(Seat.seating_category_id == pool.id, (Seat.is_blocked.is_(True)) | (Seat.guest_id.isnot(None)))
            .first()
        )
        if blocked:
            raise HTTPException(status_code=400, detail=f'"{pool.name}" has reserved or guest-assigned seats — release them first (holds belong on each night\'s own seats).')

        shared_by_other = (
            db.query(TicketType.id)
            .filter(TicketType.seating_category_id == pool.id, TicketType.id != ticket_type.id)
            .first()
        )
        # Detach first (FK to the pool must be gone before the pool is),
        # then remove the duplicate room — children before parent. With
        # autoflush off, the bulk deletes hit the DB immediately, so the
        # detach is flushed explicitly ahead of them.
        ticket_type.seating_category_id = None
        db.flush()
        if not shared_by_other:
            db.query(Seat).filter(Seat.seating_category_id == pool.id).delete()
            db.query(ZoneSection).filter(ZoneSection.seating_category_id == pool.id).delete()
            db.query(SeatingCategory).filter(SeatingCategory.id == pool.id).delete()
    else:
        ticket_type.seating_category_id = None

    ticket_type.admits = 1  # passes are seat-picked: one admission per seat
    ticket_type.valid_date = None
    db.flush()
    for m in family:
        db.add(PassMember(pass_type_id=ticket_type.id, member_type_id=m.id))
    db.commit()
    db.refresh(ticket_type)
    return _with_counts(db, [ticket_type])[0]