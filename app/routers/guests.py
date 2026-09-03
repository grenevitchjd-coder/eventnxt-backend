# eventnxt-backend: app/routers/guests.py
import secrets
from datetime import datetime, timezone

from typing import Optional

from pydantic import BaseModel
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
    GuestSeatsAssignRequest,
    GuestSentStatusRequest,
    TicketAllotmentDayItem,
)
from app.models.guest_ticket_request import GuestTicketRequest
from app.schemas.guest_ticket_request import GuestTicketRequestResponse
from app.services import comp_tickets, seating
from app.services import seats as seats_service
from app.services.deps import CurrentUser
from app.services.event_access import require_event_access

router = APIRouter(prefix="/events/{event_id}/guests", tags=["guests"])


def _validate_allotment_days(db: Session, event_id: str, items) -> None:
    """Grant days must be real event days once the event has a day list —
    a typo'd date would otherwise mint nothing and look like a bug."""
    if not items:
        return
    from app.services.comp_tickets import event_days_for

    days = event_days_for(db, event_id)
    if not days:
        return
    bad = [i.date for i in items if i.date not in days]
    if bad:
        raise HTTPException(
            status_code=400,
            detail=f'Allotment day "{bad[0]}" isn\'t one of this event\'s days ({days[0]} to {days[-1]}).',
        )

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
        section_label=guest.section_label,
        allocation_status=guest.allocation_status.value,
        party_size=guest.party_size,
        perks=guest.perks,
        comments=guest.comments,
        ticket_allotment_overridden=guest.ticket_allotment_overridden,
        ticket_allotment=[TicketAllotmentDayItem(date=r.date, quantity=r.quantity) for r in rows],
        allotment_total=allotment_total,
        allotment_distributed=allotment_distributed,
        visit_date=guest.visit_date,
        hold_timing=guest.hold_timing or "now",
        cohort_together=bool(guest.cohort_together) if guest.cohort_together is not None else True,
        allocated_by_guest_id=guest.allocated_by_guest_id,
        rsvp_token=guest.rsvp_token,
        rsvp_confirmed=guest.rsvp_confirmed,
        guest_mode=guest.guest_mode,
        effective_mode=comp_tickets.effective_guest_mode(db, guest),
        needs_seating=bool(guest.needs_seating),
        ticket_count=len(comp_tickets.valid_comp_tickets(db, guest)),
        seat_labels=[s.label for s in seats_service.guest_seats(db, guest.id)],
        link_sent_at=guest.link_sent_at,
        tickets_sent_at=guest.tickets_sent_at,
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

    effective_section_label = None
    if payload.seating_category_id:
        # Explicit override — use it directly, same single-category check
        # as before, regardless of what the guest type's priority list says.
        effective_seating_category_id = payload.seating_category_id
        effective_section_label = (payload.section_label or "").strip() or None
        if payload.allocation_status == "confirmed" or payload.hold_timing == "now":
            seating.check_capacity(db, event_id, effective_seating_category_id, party_size=payload.party_size)
        else:
            category = (
                db.query(SeatingCategory)
                .filter(SeatingCategory.id == effective_seating_category_id, SeatingCategory.event_id == event_id)
                .first()
            )
            if not category:
                raise HTTPException(status_code=404, detail="Seating category not found for this event.")
        if effective_section_label:
            # Section-level hand placement: verify the label and its room
            # (bites for confirmed guests AND pending hold-now guests —
            # holding now means the room must actually exist).
            if payload.allocation_status == "confirmed" or payload.hold_timing == "now":
                seating.check_section_capacity(
                    db, event_id, payload.seating_category_id, effective_section_label,
                    party_size=payload.party_size,
                )

    elif payload.allocation_status == "confirmed":
        # Nothing explicit — walk the guest type's priority list for the
        # first category with enough room.
        effective_seating_category_id, effective_section_label = seating.resolve_seating_placement(
            db, event_id, payload.guest_type_id, party_size=payload.party_size, visit_date=payload.visit_date
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
        section_label=effective_section_label,
        allocation_status=GuestAllocationStatus(payload.allocation_status),
        party_size=payload.party_size,
        visit_date=payload.visit_date,
        hold_timing=payload.hold_timing,
        cohort_together=payload.cohort_together,
        perks=payload.perks,
        comments=payload.comments,
        ticket_allotment_overridden=payload.ticket_allotment is not None,
        guest_mode=payload.guest_mode,
        rsvp_token=secrets.token_urlsafe(24),
    )
    db.add(guest)
    db.flush()  # assigns guest.id without committing, needed for the FK below

    if payload.ticket_allotment is not None:
        _validate_allotment_days(db, event_id, payload.ticket_allotment)
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
    new_section_label = (payload.section_label or "").strip() or None
    if payload.seating_category_id and (payload.allocation_status == "confirmed" or payload.hold_timing == "now") and not already_confirmed_here:
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

    if payload.seating_category_id and new_section_label and (payload.allocation_status == "confirmed" or payload.hold_timing == "now"):
        section_unchanged = (
            already_confirmed_here and (guest.section_label or None) == new_section_label
        )
        if not section_unchanged:
            seating.check_section_capacity(
                db, event_id, payload.seating_category_id, new_section_label,
                party_size=payload.party_size, exclude_guest_id=guest.id,
            )

    guest.name = payload.name
    guest.email = payload.email
    guest.guest_type_id = payload.guest_type_id
    guest.seating_category_id = payload.seating_category_id
    guest.section_label = new_section_label if payload.seating_category_id else None
    guest.allocation_status = GuestAllocationStatus(payload.allocation_status)
    guest.party_size = payload.party_size
    guest.visit_date = payload.visit_date
    guest.hold_timing = payload.hold_timing
    guest.cohort_together = payload.cohort_together
    guest.perks = payload.perks
    guest.comments = payload.comments
    guest.guest_mode = payload.guest_mode

    if payload.ticket_allotment is not None:
        _validate_allotment_days(db, event_id, payload.ticket_allotment)
        guest.ticket_allotment_overridden = True
        seating.replace_guest_ticket_allotment(db, guest.id, payload.ticket_allotment)

    db.commit()
    db.refresh(guest)
    return _serialize_guest(db, guest)


@router.put("/{guest_id}/seats")
def set_guest_seats(
    event_id: str,
    guest_id: str,
    payload: GuestSeatsAssignRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    """
    Wholesale-replace this guest's assigned seats (Slice B of reserved
    seats). Assigning implies reserving — the seats go off sale if they
    weren't already; releasing a seat from the guest KEEPS it reserved.
    The guest's existing comp ticket codes are re-stamped so the door
    scan and any re-sent email show the seat. Returns the updated guest
    plus the pool's full seat view so the UI repaints from one response.
    """
    guest = db.query(Guest).filter(Guest.id == guest_id, Guest.event_id == event_id).first()
    if not guest:
        raise HTTPException(status_code=404, detail="Guest not found.")
    seats_service.assign_guest_seats(db, guest=guest, seat_ids=payload.seat_ids)
    db.commit()
    db.refresh(guest)
    category = (
        db.query(SeatingCategory).filter(SeatingCategory.id == guest.seating_category_id).first()
    )
    return {
        "guest": _serialize_guest(db, guest),
        "seats": seats_service.admin_seat_statuses(db, category) if category else [],
    }


@router.patch("/{guest_id}/sent-status", response_model=GuestResponse)
def set_guest_sent_status(
    event_id: str,
    guest_id: str,
    payload: GuestSentStatusRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    """
    Manually flip one of the organizer's record-keeping markers:
    marker='link' (default) is the RSVP-link-sent stamp; marker='tickets'
    is the external-ticketing tickets-sent stamp (the organizer ordered
    real tickets on their outside platform and delivered them). Setting
    sent=true stamps the current time; sent=false clears it back to null
    (correcting an accidental click) — there's only ever one timestamp.
    """
    guest = db.query(Guest).filter(Guest.id == guest_id, Guest.event_id == event_id).first()
    if not guest:
        raise HTTPException(status_code=404, detail="Guest not found.")
    stamp = datetime.now(timezone.utc) if payload.sent else None
    if payload.marker == "tickets":
        guest.tickets_sent_at = stamp
    else:
        guest.link_sent_at = stamp
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
            db, event_id, str(guest.guest_type_id), party_size=guest.party_size, visit_date=guest.visit_date
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
            date=req.date,
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

    allot = seating.effective_allotment(db, guest)
    if req.date and allot:
        # Day-granted guest: approval grows THAT day's grant, not the
        # party — the next mint tops up exactly that day's codes.
        from app.schemas.guest import TicketAllotmentDayItem

        bumped = dict(allot)
        bumped[req.date] = bumped.get(req.date, 0) + req.quantity
        seating.replace_guest_ticket_allotment(
            db, guest.id, [TicketAllotmentDayItem(date=d, quantity=q) for d, q in sorted(bumped.items())]
        )
        guest.ticket_allotment_overridden = True
    else:
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
        current_party_size=guest.party_size, quantity=req.quantity, date=req.date, note=req.note,
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
        current_party_size=guest.party_size, quantity=req.quantity, date=req.date, note=req.note,
        status=req.status, created_at=req.created_at, resolved_at=req.resolved_at,
    )


class SyncTicketsRequest(BaseModel):
    note: Optional[str] = None  # highlighted in the resent email ("You've been upgraded to Row 1!")
    resend: bool = True


@router.post("/{guest_id}/sync-tickets", response_model=GuestResponse)
def sync_guest_tickets(
    event_id: str,
    guest_id: str,
    payload: SyncTicketsRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    """
    Re-true a guest's admission codes to their CURRENT shape, both
    directions — organizer changed their days, quantities, or seats, and
    this makes the codes match: excess voided (seatless first), missing
    minted, seats restamped, and (by default) the fresh set emailed with
    an optional highlighted note ("You've been upgraded to front row!").
    """
    guest = db.query(Guest).filter(Guest.id == guest_id, Guest.event_id == event_id).first()
    if not guest:
        raise HTTPException(status_code=404, detail="Guest not found for this event.")
    if not comp_tickets.is_native_ticketing(db, event_id):
        raise HTTPException(
            status_code=400,
            detail="Comp ticket codes only exist for events selling through EventNXT (see Event settings).",
        )
    if guest.allocation_status != GuestAllocationStatus.CONFIRMED:
        raise HTTPException(status_code=400, detail="Sync is for confirmed guests — confirm them first (or use Send ticket).")
    minted, voided = comp_tickets.sync_guest_tickets(db, guest)
    db.flush()
    tickets = comp_tickets.valid_comp_tickets(db, guest)
    if payload.resend and tickets:
        comp_tickets.send_comp_ticket_email(db, guest, tickets, note=payload.note)
    db.commit()
    db.refresh(guest)
    resp = _serialize_guest(db, guest)
    return resp


@router.delete("/{guest_id}", status_code=204)
def delete_guest(
    event_id: str,
    guest_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    """
    Fully remove a guest — including any comp codes already minted for
    them (the codes die with them; the door would show not-found), any
    pending ticket requests, and their seat assignments. Assigned seats
    stay RESERVED (blocked, label intact) so the physical hold survives
    the person — release them from the type's Seats view if the chairs
    should go back on sale. A distributor with recipients still on the
    books can't be deleted; remove or reassign the recipients first.
    """
    from app.models.guest_ticket_request import GuestTicketRequest
    from app.models.seat import Seat
    from app.models.ticket import Ticket

    guest = db.query(Guest).filter(Guest.id == guest_id, Guest.event_id == event_id).first()
    if not guest:
        raise HTTPException(status_code=404, detail="Guest not found.")
    kids = db.query(Guest.id).filter(Guest.allocated_by_guest_id == guest_id).count()
    if kids:
        raise HTTPException(
            status_code=400,
            detail=f"This allotment still has {kids} recipient(s) — remove them first so their tickets are handled deliberately.",
        )
    # Order matters with autoflush off: children before the guest row,
    # each bulk delete hitting the DB immediately.
    db.query(GuestTicketRequest).filter(GuestTicketRequest.guest_id == guest_id).delete()
    db.query(Ticket).filter(Ticket.guest_id == guest_id).delete()
    db.query(Seat).filter(Seat.guest_id == guest_id).update({Seat.guest_id: None})
    db.query(GuestTicketAllotment).filter(GuestTicketAllotment.guest_id == guest_id).delete()
    db.delete(guest)
    db.commit()

@router.get("/roster/door")
def guest_door_roster(
    event_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    """
    The door reference: every guest — direct invitees AND allotment
    recipients — with their admission codes, days, statuses, and seat
    labels in ONE payload, so door staff can look someone up by name
    when their email never arrived and admit them by punching the code
    into manual check-in. Read-only by design; fixing a guest happens
    on the Invites / Allotments pages.
    """
    from app.models.seat import Seat
    from app.models.ticket import Ticket

    guests = (
        db.query(Guest)
        .filter(Guest.event_id == event_id)
        .order_by(Guest.name)
        .all()
    )
    ids = [g.id for g in guests]
    codes_by_guest: dict = {}
    if ids:
        rows = (
            db.query(Ticket, Seat)
            .outerjoin(Seat, Seat.id == Ticket.seat_id)
            .filter(Ticket.guest_id.in_(ids))
            .order_by(Ticket.valid_date, Ticket.created_at)
            .all()
        )
        for ticket, seat in rows:
            codes_by_guest.setdefault(ticket.guest_id, []).append(
                {
                    "code": ticket.code,
                    "valid_date": ticket.valid_date,
                    "status": ticket.status.value if hasattr(ticket.status, "value") else str(ticket.status),
                    "seat_label": seat.label if seat else None,
                }
            )
    out = []
    for g in guests:
        out.append(
            {
                "id": str(g.id),
                "name": g.name,
                "email": g.email,
                "guest_type_id": str(g.guest_type_id) if g.guest_type_id else None,
                "allocation_status": g.allocation_status.value if hasattr(g.allocation_status, "value") else str(g.allocation_status),
                "rsvp_confirmed": g.rsvp_confirmed,
                "party_size": g.party_size,
                "visit_date": g.visit_date,
                "allocated_by_guest_id": str(g.allocated_by_guest_id) if g.allocated_by_guest_id else None,
                "link_sent_at": g.link_sent_at.isoformat() if g.link_sent_at else None,
                "tickets_sent_at": g.tickets_sent_at.isoformat() if g.tickets_sent_at else None,
                "tickets": codes_by_guest.get(g.id, []),
            }
        )
    return out