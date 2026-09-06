# eventnxt-backend: app/routers/seating_categories.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.guest import Guest, GuestAllocationStatus
from app.models.guest_type_seating_priority import GuestTypeSeatingPriority
from app.models.order import Order, OrderStatus
from app.models.order_item import OrderItem
from app.models.ticket_type import TicketType
from app.models.seating_category import SeatingCategory
from app.models.zone_section import ZoneSection
from app.schemas.seating_category import (
    PoolSectionAvailability,
    SectionAvailabilityRow,
    AdminSeatResponse,
    SeatBlockRequest,
    SeatUnblockRequest,
    ZoneSectionsReplaceRequest,
    ZoneSectionResponse,
    SeatingCategoryCreateRequest,
    SeatingCategoryUpdateRequest,
    SeatingCategoryResponse,
    SeatingSummaryRow,
)
from app.services import sales as sales_service
from app.services import seats as seats_service
from app.services.deps import CurrentUser
from app.services.permissions import require_setup

router = APIRouter(prefix="/events/{event_id}/seating-categories", tags=["seating-categories"])


@router.post("", response_model=SeatingCategoryResponse, status_code=201)
def create_seating_category(
    event_id: str,
    payload: SeatingCategoryCreateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_setup),
):
    category = SeatingCategory(
        event_id=event_id,
        name=payload.name,
        capacity=payload.capacity,
        sales_grain=payload.sales_grain,
        row_label=(payload.row_label or None),
        section_label=(payload.section_label or None),
        table_count=payload.table_count,
        seats_per_table=payload.seats_per_table,
    )
    db.add(category)
    db.flush()
    seats_service.sync_seats_for_pool(db, category)  # assigned pools get seats immediately
    db.commit()
    db.refresh(category)
    return category


@router.get("", response_model=list[SeatingCategoryResponse])
def list_seating_categories(
    event_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_setup),
):
    cats = db.query(SeatingCategory).filter(SeatingCategory.event_id == event_id).order_by(SeatingCategory.created_at).all()
    secs = (
        db.query(ZoneSection)
        .filter(ZoneSection.seating_category_id.in_([c.id for c in cats]))
        .order_by(ZoneSection.sort_order, ZoneSection.created_at)
        .all()
        if cats
        else []
    )
    by_cat = {}
    for sec in secs:
        by_cat.setdefault(sec.seating_category_id, []).append(sec)
    out = []
    for c in cats:
        resp = SeatingCategoryResponse.model_validate(c)
        resp.sections = [ZoneSectionResponse.model_validate(x) for x in by_cat.get(c.id, [])]
        out.append(resp)
    return out


@router.patch("/{category_id}", response_model=SeatingCategoryResponse)
def update_seating_category(
    event_id: str,
    category_id: str,
    payload: SeatingCategoryUpdateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_setup),
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
        confirmed_seats = (
            db.query(func.coalesce(func.sum(Guest.party_size), 0))
            .filter(
                Guest.seating_category_id == category.id,
                Guest.allocation_status == GuestAllocationStatus.CONFIRMED,
            )
            .scalar()
            or 0
        )
        if payload.capacity < confirmed_seats:
            raise HTTPException(
                status_code=400,
                detail=f"Can't set capacity below {confirmed_seats} — that many seats are already "
                f"confirmed in this category.",
            )

    category.name = payload.name
    category.capacity = payload.capacity
    category.sales_grain = payload.sales_grain
    category.row_label = payload.row_label or None
    category.section_label = payload.section_label or None
    category.table_count = payload.table_count
    category.seats_per_table = payload.seats_per_table
    db.flush()
    # Switching a pool to (or within) assigned seating regenerates its
    # seats from the existing section rows.
    seats_service.sync_seats_for_pool(db, category)
    db.commit()
    db.refresh(category)
    return category


@router.put("/{category_id}/sections", response_model=SeatingCategoryResponse)
def replace_zone_sections(
    event_id: str,
    category_id: str,
    payload: ZoneSectionsReplaceRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_setup),
):
    """
    Replace the pool's member sections wholesale (the composer sends the
    full list — same full-replace contract as the profile editor). The
    pool's capacity is DERIVED as the sum, keeping one true number for
    every existing consumer. An empty list removes the breakdown and
    leaves the standalone capacity as-is. Shrinking below already
    confirmed guests is refused, mirroring the plain capacity check.
    """
    category = (
        db.query(SeatingCategory)
        .filter(SeatingCategory.id == category_id, SeatingCategory.event_id == event_id)
        .first()
    )
    if not category:
        raise HTTPException(status_code=404, detail="Seating pool not found for this event.")

    new_total = sum(item.capacity for item in payload.sections)
    if payload.sections:
        confirmed_seats = (
            db.query(func.coalesce(func.sum(Guest.party_size), 0))
            .filter(
                Guest.seating_category_id == category.id,
                Guest.allocation_status == GuestAllocationStatus.CONFIRMED,
            )
            .scalar()
        )
        if new_total < confirmed_seats:
            raise HTTPException(
                status_code=400,
                detail=f"Sections total {new_total} but {confirmed_seats} seats are already confirmed here.",
            )

    db.query(ZoneSection).filter(ZoneSection.seating_category_id == category.id).delete()
    for i, item in enumerate(payload.sections):
        db.add(
            ZoneSection(
                seating_category_id=category.id,
                section_label=item.section_label.strip(),
                row_label=(item.row_label or None),
                capacity=item.capacity,
                table_count=item.table_count,
                seats_per_table=item.seats_per_table,
                sort_order=i,
            )
        )
    if payload.sections:
        category.capacity = new_total
    db.flush()
    # Assigned pools: regenerate seat records to match the new structure
    # (surviving seats re-link; sold/held seats can never be destroyed).
    seats_service.sync_seats_for_pool(db, category)
    db.commit()
    db.refresh(category)
    resp = SeatingCategoryResponse.model_validate(category)
    resp.sections = [
        ZoneSectionResponse.model_validate(x)
        for x in db.query(ZoneSection)
        .filter(ZoneSection.seating_category_id == category.id)
        .order_by(ZoneSection.sort_order)
        .all()
    ]
    return resp


@router.post("/{category_id}/fan-out", response_model=list[SeatingCategoryResponse])
def fan_out_seating_category(
    event_id: str,
    category_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_setup),
):
    """
    "Same room every day" for events with NO ticket types (external
    platform / invite-only). Clones this pool to every event day not
    already covered by a same-named dated pool: the base keeps its bare
    name and serves the FIRST night — the exact convention ticket-type
    fan-out writes and pool_for_day's name-family fallback reads —
    while clones are "<Base> (MM/DD)" with identical sections and
    freshly generated seats, fully independent per-day inventories.
    Idempotent: rerunning skips covered days. Reserved-seat holds are
    NOT copied (a press hold is a per-day decision — block each day's
    seats in its own Seats view). A pool sold by a ticket type refuses:
    its day machinery lives on the type's own fan-out.
    """
    from app.services.comp_tickets import event_days_for
    from app.services.seating import POOL_DAY_SUFFIX, normalized_name, pool_name_day

    category = (
        db.query(SeatingCategory)
        .filter(SeatingCategory.id == category_id, SeatingCategory.event_id == event_id)
        .first()
    )
    if not category:
        raise HTTPException(status_code=404, detail="Seating pool not found for this event.")
    if db.query(TicketType.id).filter(TicketType.seating_category_id == category.id).first():
        raise HTTPException(
            status_code=400,
            detail="This area is sold by a ticket type — use the ticket type's own "
            "\u201cCreate for every day\u201d instead.",
        )
    if POOL_DAY_SUFFIX.search(str(category.name or "")):
        raise HTTPException(
            status_code=400,
            detail="Fan out from the base area — the one without a day in its name.",
        )
    days = event_days_for(db, event_id)
    if len(days) < 2:
        raise HTTPException(
            status_code=400,
            detail="This event has no multi-day list — set span and days in Event settings.",
        )

    # Days already covered by a same-named dated pool (normalized names,
    # same as every other family-discovery site).
    family_norm = normalized_name(category.name)
    covered = set()
    for sib in db.query(SeatingCategory).filter(SeatingCategory.event_id == event_id).all():
        sm = POOL_DAY_SUFFIX.search(str(sib.name or ""))
        if not sm:
            continue
        if normalized_name((sib.name or "")[: sm.start()]) != family_norm:
            continue
        sib_day = pool_name_day(sib.name, days)
        if sib_day:
            covered.add(sib_day)

    sections = (
        db.query(ZoneSection)
        .filter(ZoneSection.seating_category_id == category.id)
        .order_by(ZoneSection.sort_order)
        .all()
    )
    created = []
    for day in days[1:]:  # the bare base IS the first night's room
        if day in covered:
            continue
        clone = SeatingCategory(
            event_id=event_id,
            name=f"{category.name} ({day[5:7]}/{day[8:10]})",
            capacity=category.capacity,
            sales_grain=category.sales_grain,
            row_label=category.row_label,
            section_label=category.section_label,
            table_count=category.table_count,
            seats_per_table=category.seats_per_table,
        )
        db.add(clone)
        db.flush()
        for i, sec in enumerate(sections):
            db.add(
                ZoneSection(
                    seating_category_id=clone.id,
                    section_label=sec.section_label,
                    row_label=sec.row_label,
                    capacity=sec.capacity,
                    table_count=sec.table_count,
                    seats_per_table=sec.seats_per_table,
                    sort_order=i,
                )
            )
        db.flush()
        seats_service.sync_seats_for_pool(db, clone)
        created.append(clone)
    db.commit()

    out = []
    for clone in created:
        db.refresh(clone)
        resp = SeatingCategoryResponse.model_validate(clone)
        resp.sections = [
            ZoneSectionResponse.model_validate(x)
            for x in db.query(ZoneSection)
            .filter(ZoneSection.seating_category_id == clone.id)
            .order_by(ZoneSection.sort_order)
            .all()
        ]
        out.append(resp)
    return out


def _pool_or_404(db: Session, event_id: str, category_id: str) -> SeatingCategory:
    category = (
        db.query(SeatingCategory)
        .filter(SeatingCategory.id == category_id, SeatingCategory.event_id == event_id)
        .first()
    )
    if not category:
        raise HTTPException(status_code=404, detail="Seating pool not found for this event.")
    return category


@router.get("/{category_id}/seats", response_model=list[AdminSeatResponse])
def list_pool_seats(
    event_id: str,
    category_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_setup),
):
    """The organizer's seat view: every seat with its derived status
    (available / sold / held / reserved) plus the reservation label.
    Only assigned pools have seats; other grains return []."""
    category = _pool_or_404(db, event_id, category_id)
    return seats_service.admin_seat_statuses(db, category)


@router.post("/{category_id}/seats/block", response_model=list[AdminSeatResponse])
def block_pool_seats(
    event_id: str,
    category_id: str,
    payload: SeatBlockRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_setup),
):
    """Reserve seats with an optional label ("Press"). Refuses seats a
    buyer already owns or holds; races with checkout are settled under
    FOR UPDATE — exactly one winner. Returns the full refreshed seat
    view so the UI repaints from one response."""
    category = _pool_or_404(db, event_id, category_id)
    seats_service.block_seats(db, category=category, seat_ids=payload.seat_ids, label=payload.label)
    db.commit()
    return seats_service.admin_seat_statuses(db, category)


@router.post("/{category_id}/seats/unblock", response_model=list[AdminSeatResponse])
def unblock_pool_seats(
    event_id: str,
    category_id: str,
    payload: SeatUnblockRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_setup),
):
    """Release organizer holds — the seats go straight back on sale."""
    category = _pool_or_404(db, event_id, category_id)
    seats_service.unblock_seats(db, category=category, seat_ids=payload.seat_ids)
    db.commit()
    return seats_service.admin_seat_statuses(db, category)


@router.delete("/{category_id}", status_code=204)
def delete_seating_category(
    event_id: str,
    category_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_setup),
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
    db.query(GuestTypeSeatingPriority).filter(
        GuestTypeSeatingPriority.seating_category_id == category_id
    ).delete()

    db.delete(category)
    db.commit()


@router.get("/summary", response_model=list[SeatingSummaryRow])
def get_seating_summary(
    event_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_setup),
):
    """
    One row per seating category, reconciling capacity against the guest
    list and box office sales in a single view — capacity, box_office,
    allotted, and committed are each queried independently and never
    double-counted against each other, since Sale records and Guest
    records are entirely separate data sources with no overlap today.
    """
    categories = db.query(SeatingCategory).filter(SeatingCategory.event_id == event_id).all()

    rows = []
    for category in categories:
        # Box office counts HEADS. Native sales: paid order items × the
        # ticket type's `admits` (a $400 table admitting 4 is 4 heads
        # against this pool). Imported sales count via the shared
        # imported_heads_for_pool (0049 stamp + legacy normalized-name
        # fallback; native Sale rows and non-admission rows excluded
        # there, so nothing double-counts or inflates by admits).
        csv_heads = sales_service.imported_heads_for_pool(db, category)
        native_heads = (
            db.query(func.coalesce(func.sum(OrderItem.quantity * TicketType.admits), 0))
            .join(TicketType, TicketType.id == OrderItem.ticket_type_id)
            .join(Order, Order.id == OrderItem.order_id)
            .filter(
                Order.event_id == event_id,
                Order.status == OrderStatus.PAID,
                TicketType.seating_category_id == category.id,
            )
            .scalar()
            or 0
        )
        box_office = csv_heads + native_heads
        # Guest heads via the day/family-aware counter the sales side
        # uses (guest_hold_heads): a guest homed at any family sibling
        # counts against THIS pool's day with that day's grant — so a
        # Friday comp shows on the Friday clone, and a 2/2/2 multi-night
        # guest shows 2 on every night's pool. Seat-holding guests are
        # excluded there (their seats consume), so blocked seats are
        # added back: assigned-to-guest seats are committed, labeled
        # unassigned blocks are reserved exposure (allotted).
        from app.models.seat import Seat
        from app.services.seating import guest_hold_heads

        assigned_seats = (
            db.query(func.count(Seat.id))
            .filter(Seat.seating_category_id == category.id, Seat.guest_id.isnot(None))
            .scalar()
            or 0
        )
        labeled_blocks = (
            db.query(func.count(Seat.id))
            .filter(
                Seat.seating_category_id == category.id,
                Seat.is_blocked.is_(True),
                Seat.guest_id.is_(None),
            )
            .scalar()
            or 0
        )
        allotted = guest_hold_heads(db, category, status_mode="offered") + assigned_seats + labeled_blocks
        committed = guest_hold_heads(db, category, status_mode="confirmed") + assigned_seats
        rows.append(
            SeatingSummaryRow(
                category_id=category.id,
                category_name=category.name,
                capacity=category.capacity,
                box_office=box_office,
                allotted=allotted,
                committed=committed,
                confirmed_avail=max(category.capacity - committed - box_office, 0),
                estimated_avail=max(category.capacity - allotted - box_office, 0),
            )
        )
    return rows

@router.get("/section-summary", response_model=list[PoolSectionAvailability])
def get_section_summary(
    event_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_setup),
):
    """
    The pool summary decomposed one level: every pool with a row per
    section — capacity, bought (box office), given (comps), left — where
    `left` is computed by the SAME function checkout and comp placement
    enforce, so this page can never disagree with what a buyer or a
    placed recipient experiences. Sectionless pools return one row.
    """
    from app.services.seating import section_availability

    categories = (
        db.query(SeatingCategory)
        .filter(SeatingCategory.event_id == event_id)
        .order_by(SeatingCategory.name)
        .all()
    )
    return [
        PoolSectionAvailability(
            category_id=c.id,
            category_name=c.name,
            sales_grain=c.sales_grain or "ga",
            capacity=c.capacity,
            sections=[SectionAvailabilityRow(**row) for row in section_availability(db, c)],
        )
        for c in categories
    ]


@router.get("/holds/labeled")
def labeled_seat_holds(
    event_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_setup),
):
    """
    Every LABELED reserved-seat hold in the event, grouped — so the
    Invites page can wave a flag on a guest's row when seats are
    already being held under their name ("Carey Grant × 2 in Row 1
    Front Center") and walk the organizer straight into claiming them.
    Unassigned blocks only; a seat already assigned to a guest is that
    guest's business.
    """
    from app.models.seat import Seat

    rows = (
        db.query(Seat.block_label, Seat.seating_category_id, func.count(Seat.id))
        .join(SeatingCategory, SeatingCategory.id == Seat.seating_category_id)
        .filter(
            SeatingCategory.event_id == event_id,
            Seat.is_blocked.is_(True),
            Seat.guest_id.is_(None),
            Seat.block_label.isnot(None),
            Seat.block_label != "",
        )
        .group_by(Seat.block_label, Seat.seating_category_id)
        .all()
    )
    pools = {c.id: c.name for c in db.query(SeatingCategory).filter(SeatingCategory.event_id == event_id).all()}
    return [
        {
            "block_label": label,
            "seating_category_id": str(cat_id),
            "pool_name": pools.get(cat_id, ""),
            "count": int(n),
        }
        for label, cat_id, n in rows
    ]