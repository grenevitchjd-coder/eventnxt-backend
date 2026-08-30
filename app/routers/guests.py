# eventnxt-backend: app/routers/guests.py
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.guest import Guest, GuestAllocationStatus
from app.models.guest_ticket_allotment import GuestTicketAllotment
from app.models.guest_type import GuestType
from app.models.guest_type_seating_priority import GuestTypeSeatingPriority
from app.models.seating_category import SeatingCategory
from app.schemas.guest import (
    GuestCreateRequest,
    GuestUpdateRequest,
    GuestResponse,
    GuestSentStatusRequest,
    TicketAllotmentDayItem,
)
from app.models.guest_ticket_request import GuestTicketRequest
from app.schemas.guest_ticket_request import GuestTicketRequestResponse
from app.services import comp_tickets, seating
from app.services.deps import CurrentUser
from app.services.event_access import require_event_access

router = APIRouter(prefix="/events/{event_id}/guests", tags=["guests"])


def _serialize_guest(db: Session, guest: Guest) -> GuestResponse:
    """
    GuestResponse.ticket_allotment isn't a raw column — it's this guest's
    own override rows (empty if not overridden), and allotment_total /
    allotment_distributed are computed aggregates — none of these come
    from automatic ORM-to-schema conversion, so they're fetched and
    assembled explicitly here.
    """
    rows = db.query(GuestTicketAllotment).filter(GuestTicketAllotment.guest_id == guest.id).all()
    allotment_total, allotment_distributed = seating.allotment_summary(db, guest)
    return GuestResponse(
        id=guest.id,
        event_id=guest.event_id,
        name=guest.name,
        email=guest.email,
        guest_type_id=guest.guest_type_id,
        seating_category_id=guest.seating_category_id,
        allocation_status=guest.allocation_status.value,
        party_size=guest.party_size,
        perks=guest.perks,
        comments=guest.comments,
        ticket_allotment_overridden=guest.ticket_allotment_overridden,
        ticket_allotment=[TicketAllotmentDayItem(date=r.date, quantity=r.quantity) for r in rows],
        allotment_total=allotment_total,
        allotment_distributed=allotment_distributed,
        visit_date=guest.visit_date,
        allocated_by_guest_id=guest.allocated_by_guest_id,
        rsvp_token=guest.rsvp_token,
        rsvp_confirmed=guest.rsvp_confirmed,
        guest_mode=guest.guest_mode,
        effective_mode=comp_tickets.effective_guest_mode(db, guest),
        needs_seating=bool(guest.needs_seating),
        ticket_count=len(comp_tickets.valid_comp_tickets(db, guest)),
        link_sent_at=guest.link_sent_at,
        created_at=guest.created_at,
    )



@router.post("", response_model=GuestResponse, status_code=201)
def create_guest(
    event_id: str,
    payload: GuestCreateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    guest_type = db.query(GuestType).filter(GuestType.id == payload.guest_type_id).first()
    if not guest_type or str(guest_type.event_id) != event_id:
        raise HTTPException(status_code=404, detail="Guest type not found for this event.")

    if payload.seating_category_id:
        # Explicit override — use it directly, same single-category check
        # as before, regardless of what the guest type's priority list says.
        effective_seating_category_id = payload.seating_category_id
        if payload.allocation_status == "confirmed":
            seating.check_capacity(db, event_id, effective_seating_category_id, party_size=payload.party_size)
        else:
            category = (
                db.query(SeatingCategory)
                .filter(SeatingCategory.id == effective_seating_category_id, SeatingCategory.event_id == event_id)
                .first()
            )
            if not category:
                raise HTTPException(status_code=404, detail="Seating category not found for this event.")

    elif payload.allocation_status == "confirmed":
        # Nothing explicit — walk the guest type's priority list for the
        # first category with enough room.
        effective_seating_category_id = seating.resolve_seating_from_priorities(
            db, event_id, payload.guest_type_id, party_size=payload.party_size
        )
        if effective_seating_category_id is None:
            if seating.has_seating_priorities(db, payload.guest_type_id):
                raise HTTPException(
                    status_code=400,
                    detail="All preferred seating categories for this guest type are full.",
                )
            # else: no priorities configured at all — guest is simply unassigned, that's fine

    else:
        # Pending/declined, nothing explicit — use the top priority as a
        # placeholder (no capacity check needed, nothing is held yet, so
        # this is just "which category would they land in first" — not a
        # search for one with room).
        first_priority = (
            db.query(GuestTypeSeatingPriority)
            .filter(GuestTypeSeatingPriority.guest_type_id == payload.guest_type_id)
            .order_by(GuestTypeSeatingPriority.priority_order)
            .first()
        )
        effective_seating_category_id = first_priority.seating_category_id if first_priority else None

    guest = Guest(
        event_id=event_id,
        name=payload.name,
        email=payload.email,
        guest_type_id=payload.guest_type_id,
        seating_category_id=effective_seating_category_id,
        allocation_status=GuestAllocationStatus(payload.allocation_status),
        party_size=payload.party_size,
        visit_date=payload.visit_date,
        perks=payload.perks,
        comments=payload.comments,
        ticket_allotment_overridden=payload.ticket_allotment is not None,
        guest_mode=payload.guest_mode,
        rsvp_token=secrets.token_urlsafe(24),
    )
    db.add(guest)
    db.flush()  # assigns guest.id without committing, needed for the FK below

    if payload.ticket_allotment is not None:
        seating.replace_guest_ticket_allotment(db, guest.id, payload.ticket_allotment)

    # Auto-send delivery (Event settings): the moment an invite/select
    # guest with a resolved seat is added to a native-ticketing event,
    # their admission codes mint and email — no RSVP round-trip. Guests
    # whose seat couldn't resolve, and distribute-mode holders, are
    # untouched (holders hand out tickets; they don't hold one).
    if (
        comp_tickets.comp_delivery_for(db, event_id) == "auto_send"
        and comp_tickets.is_native_ticketing(db, event_id)
        and guest.allocation_status == GuestAllocationStatus.CONFIRMED
        and comp_tickets.effective_guest_mode(db, guest) in ("invite", "select")
    ):
        tickets = comp_tickets.issue_comp_tickets(db, guest)
        db.flush()
        comp_tickets.send_comp_ticket_email(db, guest, tickets)

    db.commit()
    db.refresh(guest)
    return _serialize_guest(db, guest)


@router.get("", response_model=list[GuestResponse])
def list_guests(
    event_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    guests = db.query(Guest).filter(Guest.event_id == event_id).all()
    return [_serialize_guest(db, g) for g in guests]


@router.patch("/{guest_id}", response_model=GuestResponse)
def update_guest(
    event_id: str,
    guest_id: str,
    payload: GuestUpdateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    """
    Editing always takes an explicit seating_category_id (or null) — no
    priority-list magic here, unlike creation. Someone editing a specific
    existing guest is making a deliberate choice, not asking the system
    to decide for them. ticket_allotment works the same way as on create:
    omit it to leave the guest's existing override (or lack of one)
    untouched; provide a list to replace it.
    """
    guest = db.query(Guest).filter(Guest.id == guest_id, Guest.event_id == event_id).first()
    if not guest:
        raise HTTPException(status_code=404, detail="Guest not found.")

    guest_type = db.query(GuestType).filter(GuestType.id == payload.guest_type_id).first()
    if not guest_type or str(guest_type.event_id) != event_id:
        raise HTTPException(status_code=404, detail="Guest type not found for this event.")

    already_confirmed_here = (
        guest.allocation_status == GuestAllocationStatus.CONFIRMED
        and str(guest.seating_category_id) == str(payload.seating_category_id)
        and guest.party_size == payload.party_size
    )
    if payload.seating_category_id and payload.allocation_status == "confirmed" and not already_confirmed_here:
        seating.check_capacity(
            db, event_id, payload.seating_category_id, party_size=payload.party_size, exclude_guest_id=guest.id
        )
    elif payload.seating_category_id and not already_confirmed_here:
        category = (
            db.query(SeatingCategory)
            .filter(SeatingCategory.id == payload.seating_category_id, SeatingCategory.event_id == event_id)
            .first()
        )
        if not category:
            raise HTTPException(status_code=404, detail="Seating category not found for this event.")

    guest.name = payload.name
    guest.email = payload.email
    guest.guest_type_id = payload.guest_type_id
    guest.seating_category_id = payload.seating_category_id
    guest.allocation_status = GuestAllocationStatus(payload.allocation_status)
    guest.party_size = payload.party_size
    guest.visit_date = payload.visit_date
    guest.perks = payload.perks
    guest.comments = payload.comments
    guest.guest_mode = payload.guest_mode

    if payload.ticket_allotment is not None:
        guest.ticket_allotment_overridden = True
        seating.replace_guest_ticket_allotment(db, guest.id, payload.ticket_allotment)

    db.commit()
    db.refresh(guest)
    return _serialize_guest(db, guest)


@router.patch("/{guest_id}/sent-status", response_model=GuestResponse)
def set_guest_sent_status(
    event_id: str,
    guest_id: str,
    payload: GuestSentStatusRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    """
    Manually mark whether this guest's RSVP link has been sent — there's
    no automated email yet, so this is the organizer's own record-keeping.
    Setting sent=true stamps the current time; sent=false clears it back
    to null (e.g. correcting an accidental click), not a "sent then
    un-sent" history — there's only ever one timestamp.
    """
    guest = db.query(Guest).filter(Guest.id == guest_id, Guest.event_id == event_id).first()
    if not guest:
        raise HTTPException(status_code=404, detail="Guest not found.")
    guest.link_sent_at = datetime.now(timezone.utc) if payload.sent else None
    db.commit()
    db.refresh(guest)
    return _serialize_guest(db, guest)


@router.post("/{guest_id}/send-ticket", response_model=GuestResponse)
def send_ticket(
    event_id: str,
    guest_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    """
    The Needs-seating queue's resolve button — and a plain re-send for
    any guest. Assign seating first (PATCH the guest, or let this walk
    the priority list), then this confirms them, mints any missing comp
    tickets up to party_size, and emails the codes.
    """
    guest = db.query(Guest).filter(Guest.id == guest_id, Guest.event_id == event_id).first()
    if not guest:
        raise HTTPException(status_code=404, detail="Guest not found for this event.")

    if not comp_tickets.is_native_ticketing(db, event_id):
        raise HTTPException(
            status_code=400,
            detail="Comp ticket codes only exist for events selling through EventNXT (see Event settings).",
        )

    if guest.seating_category_id is None:
        resolved = seating.resolve_seating_from_priorities(
            db, event_id, str(guest.guest_type_id), party_size=guest.party_size
        )
        if resolved is None and seating.has_seating_priorities(db, str(guest.guest_type_id)):
            raise HTTPException(
                status_code=400,
                detail="Still no room in this guest type's sections — raise a capacity or assign a section on the guest first.",
            )
        guest.seating_category_id = resolved
    elif guest.allocation_status != GuestAllocationStatus.CONFIRMED:
        # Explicit section on the guest — honor it, but never over capacity.
        seating.check_capacity(db, event_id, guest.seating_category_id, party_size=guest.party_size, exclude_guest_id=guest.id)

    guest.allocation_status = GuestAllocationStatus.CONFIRMED
    guest.needs_seating = False
    tickets = comp_tickets.issue_comp_tickets(db, guest)
    db.flush()
    sent = comp_tickets.send_comp_ticket_email(db, guest, tickets)
    if sent:
        guest.link_sent_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(guest)
    return _serialize_guest(db, guest)


@router.get("/ticket-requests/all", response_model=list[GuestTicketRequestResponse])
def list_ticket_requests(
    event_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    rows = (
        db.query(GuestTicketRequest, Guest)
        .join(Guest, Guest.id == GuestTicketRequest.guest_id)
        .filter(Guest.event_id == event_id)
        .order_by(GuestTicketRequest.status.desc(), GuestTicketRequest.created_at.desc())
        .all()
    )
    return [
        GuestTicketRequestResponse(
            id=req.id,
            guest_id=g.id,
            guest_name=g.name,
            guest_email=g.email,
            current_party_size=g.party_size,
            quantity=req.quantity,
            note=req.note,
            status=req.status,
            created_at=req.created_at,
            resolved_at=req.resolved_at,
        )
        for req, g in rows
    ]


@router.post("/ticket-requests/{request_id}/approve", response_model=GuestTicketRequestResponse)
def approve_ticket_request(
    event_id: str,
    request_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    """
    Approve = the guest's party grows by the requested amount. If they're
    already confirmed with a seat on a native-ticketing event, the extra
    admission codes mint and email immediately; otherwise they arrive
    through the normal confirm path. Capacity: a confirmed guest's grown
    party is re-checked against their section before anything changes.
    """
    row = (
        db.query(GuestTicketRequest, Guest)
        .join(Guest, Guest.id == GuestTicketRequest.guest_id)
        .filter(GuestTicketRequest.id == request_id, Guest.event_id == event_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Request not found for this event.")
    req, guest = row
    if req.status != "pending":
        raise HTTPException(status_code=400, detail="This request was already resolved.")

    new_party = guest.party_size + req.quantity
    if guest.allocation_status == GuestAllocationStatus.CONFIRMED and guest.seating_category_id:
        seating.check_capacity(
            db, event_id, guest.seating_category_id, party_size=new_party, exclude_guest_id=guest.id
        )
    guest.party_size = new_party
    comp_tickets.mark_resolved(req, "approved")

    if (
        guest.allocation_status == GuestAllocationStatus.CONFIRMED
        and comp_tickets.is_native_ticketing(db, event_id)
        and comp_tickets.valid_comp_tickets(db, guest)
    ):
        tickets = comp_tickets.issue_comp_tickets(db, guest)
        db.flush()
        comp_tickets.send_comp_ticket_email(db, guest, tickets)

    db.commit()
    db.refresh(req)
    return GuestTicketRequestResponse(
        id=req.id, guest_id=guest.id, guest_name=guest.name, guest_email=guest.email,
        current_party_size=guest.party_size, quantity=req.quantity, note=req.note,
        status=req.status, created_at=req.created_at, resolved_at=req.resolved_at,
    )


@router.post("/ticket-requests/{request_id}/deny", response_model=GuestTicketRequestResponse)
def deny_ticket_request(
    event_id: str,
    request_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    row = (
        db.query(GuestTicketRequest, Guest)
        .join(Guest, Guest.id == GuestTicketRequest.guest_id)
        .filter(GuestTicketRequest.id == request_id, Guest.event_id == event_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Request not found for this event.")
    req, guest = row
    if req.status != "pending":
        raise HTTPException(status_code=400, detail="This request was already resolved.")
    comp_tickets.mark_resolved(req, "denied")
    db.commit()
    db.refresh(req)
    return GuestTicketRequestResponse(
        id=req.id, guest_id=guest.id, guest_name=guest.name, guest_email=guest.email,
        current_party_size=guest.party_size, quantity=req.quantity, note=req.note,
        status=req.status, created_at=req.created_at, resolved_at=req.resolved_at,
    )


@router.delete("/{guest_id}", status_code=204)
def delete_guest(
    event_id: str,
    guest_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    guest = db.query(Guest).filter(Guest.id == guest_id, Guest.event_id == event_id).first()
    if not guest:
        raise HTTPException(status_code=404, detail="Guest not found.")
    db.query(GuestTicketAllotment).filter(GuestTicketAllotment.guest_id == guest_id).delete()
    db.delete(guest)
    db.commit()