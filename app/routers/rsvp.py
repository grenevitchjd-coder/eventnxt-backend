import secrets

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.guest import Guest, GuestAllocationStatus
from app.models.guest_type import GuestType
from app.schemas.rsvp import (
    DistributedRecipient,
    RSVPDistributeRequest,
    RSVPInfoResponse,
    RSVPRespondRequest,
)
from app.services import seating

router = APIRouter(tags=["rsvp"])


def _get_guest_by_token_or_404(db: Session, token: str) -> Guest:
    guest = db.query(Guest).filter(Guest.rsvp_token == token).first()
    if not guest:
        raise HTTPException(status_code=404, detail="That RSVP link isn't valid.")
    return guest


@router.get("/public/rsvp/{token}", response_model=RSVPInfoResponse)
def get_rsvp_info(token: str, db: Session = Depends(get_db)):
    guest = _get_guest_by_token_or_404(db, token)
    guest_type = db.query(GuestType).filter(GuestType.id == guest.guest_type_id).first()

    ticket_count, valid_dates = seating.effective_allotment(guest, guest_type)
    is_allotment_holder = ticket_count is not None and ticket_count > 0

    if not is_allotment_holder:
        return RSVPInfoResponse(
            guest_name=guest.name,
            guest_type_name=guest_type.name,
            allocation_status=guest.allocation_status.value,
            visit_date=guest.visit_date,
            party_size=guest.party_size,
            is_allotment_holder=False,
        )

    children = db.query(Guest).filter(Guest.allocated_by_guest_id == guest.id).all()
    distributed_total = sum(c.party_size for c in children)

    return RSVPInfoResponse(
        guest_name=guest.name,
        guest_type_name=guest_type.name,
        allocation_status=guest.allocation_status.value,
        visit_date=guest.visit_date,
        party_size=guest.party_size,
        is_allotment_holder=True,
        ticket_count=ticket_count,
        valid_dates=valid_dates,
        tickets_distributed=distributed_total,
        tickets_remaining=max(ticket_count - distributed_total, 0),
        distributed_recipients=[
            DistributedRecipient(
                name=c.name,
                email=c.email,
                visit_date=c.visit_date,
                party_size=c.party_size,
                allocation_status=c.allocation_status.value,
            )
            for c in children
        ],
    )


@router.post("/public/rsvp/{token}/respond", response_model=RSVPInfoResponse)
def respond_to_rsvp(token: str, payload: RSVPRespondRequest, db: Session = Depends(get_db)):
    """
    The simple confirm/decline path — for an ordinary guest, or a
    delegated recipient confirming the specific ticket someone gave them.
    Not for allotment holders — they use /distribute instead.
    """
    guest = _get_guest_by_token_or_404(db, token)
    guest_type = db.query(GuestType).filter(GuestType.id == guest.guest_type_id).first()

    ticket_count, _ = seating.effective_allotment(guest, guest_type)
    if ticket_count is not None and ticket_count > 0:
        raise HTTPException(
            status_code=400, detail="This link is for distributing tickets, not a simple RSVP — use /distribute."
        )

    if payload.attending:
        new_category_id = seating.resolve_seating_from_priorities(
            db, str(guest.event_id), str(guest.guest_type_id), party_size=guest.party_size
        )
        if new_category_id is None and seating.has_seating_priorities(db, str(guest.guest_type_id)):
            raise HTTPException(status_code=400, detail="Sorry — there's no room left for this event.")
        guest.seating_category_id = new_category_id
        guest.allocation_status = GuestAllocationStatus.CONFIRMED
        guest.rsvp_confirmed = "yes"
    else:
        guest.allocation_status = GuestAllocationStatus.DECLINED
        guest.rsvp_confirmed = "no"

    db.commit()
    db.refresh(guest)
    return get_rsvp_info(token, db)


@router.post("/public/rsvp/{token}/distribute", response_model=RSVPInfoResponse)
def distribute_tickets(token: str, payload: RSVPDistributeRequest, db: Session = Depends(get_db)):
    """
    An allotment holder (model, sponsor) naming who gets their tickets.
    Only checks the TICKET COUNT limit here — each named recipient still
    gets their own RSVP link and personally confirms via /respond, which
    is when their actual seat is resolved and checked. This mirrors how
    an organizer-added guest starts pending and only consumes a seat once
    confirmed — nothing here holds a seat until the recipient says yes.
    """
    guest = _get_guest_by_token_or_404(db, token)
    guest_type = db.query(GuestType).filter(GuestType.id == guest.guest_type_id).first()

    ticket_count, valid_dates = seating.effective_allotment(guest, guest_type)
    if ticket_count is None or ticket_count <= 0:
        raise HTTPException(status_code=400, detail="This link doesn't have tickets to distribute.")

    if not payload.recipients:
        raise HTTPException(status_code=400, detail="Add at least one recipient.")

    for r in payload.recipients:
        if valid_dates and r.visit_date and r.visit_date not in valid_dates:
            raise HTTPException(
                status_code=400, detail=f'"{r.visit_date}" isn\'t one of the valid dates for these tickets.'
            )

    requested_total = sum(r.party_size for r in payload.recipients)
    seating.check_allotment_capacity(db, str(guest.id), requested_total, ticket_count)

    for r in payload.recipients:
        db.add(
            Guest(
                event_id=guest.event_id,
                name=r.name,
                email=r.email,
                guest_type_id=guest.guest_type_id,
                seating_category_id=None,  # resolved when the recipient confirms, not now
                allocation_status=GuestAllocationStatus.PENDING,
                party_size=r.party_size,
                visit_date=r.visit_date,
                allocated_by_guest_id=guest.id,
                # Explicit 0, not left as None — None would inherit the
                # guest type's default allotment and wrongly make this
                # recipient look like a distributor themselves. A
                # delegated recipient is always terminal: they confirm
                # for themselves, they don't get to redistribute further.
                allotment_ticket_count=0,
                rsvp_token=secrets.token_urlsafe(24),
            )
        )

    db.commit()
    return get_rsvp_info(token, db)