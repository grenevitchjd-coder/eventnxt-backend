# eventnxt-backend: app/routers/seating_categories.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.guest import Guest, GuestAllocationStatus
from app.models.guest_type_seating_priority import GuestTypeSeatingPriority
from app.models.order import Order, OrderStatus
from app.models.order_item import OrderItem
from app.models.sale import Sale, SaleSource
from app.models.ticket_type import TicketType
from app.models.seating_category import SeatingCategory
from app.models.zone_section import ZoneSection
from app.schemas.seating_category import (
    ZoneSectionsReplaceRequest,
    ZoneSectionResponse,
    SeatingCategoryCreateRequest,
    SeatingCategoryUpdateRequest,
    SeatingCategoryResponse,
    SeatingSummaryRow,
)
from app.services import seats as seats_service
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
    user: CurrentUser = Depends(require_event_access),
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
    user: CurrentUser = Depends(require_event_access),
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
    db.query(GuestTypeSeatingPriority).filter(
        GuestTypeSeatingPriority.seating_category_id == category_id
    ).delete()

    db.delete(category)
    db.commit()


@router.get("/summary", response_model=list[SeatingSummaryRow])
def get_seating_summary(
    event_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
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
        # against this pool). CSV-imported sales predate admits and
        # count as-is. Native Sale rows are EXCLUDED here to avoid
        # double-counting — they still record units for promo/referral
        # math, which must never be inflated by admits.
        csv_heads = (
            db.query(func.coalesce(func.sum(Sale.quantity), 0))
            .filter(
                Sale.event_id == event_id,
                Sale.ticket_type.ilike(category.name),
                Sale.source != SaleSource.NATIVE,
            )
            .scalar()
            or 0
        )
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
        allotted = (
            db.query(func.coalesce(func.sum(Guest.party_size), 0))
            .filter(
                Guest.seating_category_id == category.id,
                Guest.allocation_status.in_([GuestAllocationStatus.PENDING, GuestAllocationStatus.CONFIRMED]),
            )
            .scalar()
            or 0
        )
        committed = (
            db.query(func.coalesce(func.sum(Guest.party_size), 0))
            .filter(
                Guest.seating_category_id == category.id,
                Guest.allocation_status == GuestAllocationStatus.CONFIRMED,
            )
            .scalar()
            or 0
        )
        rows.append(
            SeatingSummaryRow(
                category_id=category.id,
                category_name=category.name,
                capacity=category.capacity,
                box_office=box_office,
                allotted=allotted,
                committed=committed,
                confirmed_avail=max(category.capacity - committed, 0),
                estimated_avail=max(category.capacity - allotted - box_office, 0),
            )
        )
    return rows