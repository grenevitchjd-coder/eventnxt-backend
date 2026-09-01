# eventnxt-backend: app/routers/check_in.py
"""
Door check-in. One endpoint redeems a scanned (or typed) code, one
serves the running tally.

Design choices worth naming:
- Redemption always answers 200 with a `result` field
  ('admitted' | 'already_checked_in' | 'refunded' | 'not_found') plus
  whatever is known about the ticket. A door scanner needs to SHOW who
  the dupe was and when they went in — an HTTP error with a bare detail
  string can't carry that.
- Once-only is enforced under SELECT ... FOR UPDATE on the ticket row:
  two doors scanning the same code serialize, exactly one admit.
- checked_in_at is a timestamp, not a status — VALID/REFUNDED semantics
  (seat release, Find My Tickets, refunds) stay untouched.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.guest import Guest
from app.models.guest_type import GuestType
from app.models.order import Order
from app.models.order_item import OrderItem
from app.models.seat import Seat
from app.models.ticket import Ticket, TicketStatus
from app.services.deps import CurrentUser
from app.services.event_access import require_event_access

router = APIRouter(prefix="/events/{event_id}/check-in", tags=["check-in"])


class CheckInResult(BaseModel):
    result: str  # 'admitted' | 'already_checked_in' | 'refunded' | 'wrong_day' | 'not_found'
    code: str
    valid_date: Optional[str] = None  # the day a dated code admits on
    name: Optional[str] = None
    kind: Optional[str] = None  # 'order' | 'comp'
    ticket_type_name: Optional[str] = None
    seat_label: Optional[str] = None
    checked_in_at: Optional[datetime] = None
    party_note: Optional[str] = None  # comps: "code 2 of 4 for this guest"


class CheckInStats(BaseModel):
    total_valid: int
    checked_in: int
    recent: list[CheckInResult] = []


def _describe(db: Session, ticket: Ticket) -> dict:
    """Everything the door needs to greet the person behind a code."""
    out: dict = {"code": ticket.code, "valid_date": ticket.valid_date}
    if ticket.guest_id:
        guest = db.query(Guest).filter(Guest.id == ticket.guest_id).first()
        gt = db.query(GuestType).filter(GuestType.id == guest.guest_type_id).first() if guest else None
        siblings = (
            db.query(Ticket)
            .filter(Ticket.guest_id == ticket.guest_id, Ticket.status == TicketStatus.VALID)
            .order_by(Ticket.created_at)
            .all()
        )
        idx = next((i + 1 for i, t in enumerate(siblings) if t.id == ticket.id), None)
        comp_seat_label = None
        if ticket.seat_id:
            seat = db.query(Seat).filter(Seat.id == ticket.seat_id).first()
            comp_seat_label = seat.label if seat else None
        if not comp_seat_label and guest and guest.section_label:
            # Section-placed comp with no specific seat: the door still
            # gets a destination.
            comp_seat_label = f"Section {guest.section_label}"
        out.update(
            kind="comp",
            name=guest.name if guest else None,
            ticket_type_name=(gt.name if gt else None),
            seat_label=comp_seat_label,
            party_note=(f"code {idx} of {len(siblings)} for this guest" if idx and len(siblings) > 1 else None),
        )
    else:
        order = db.query(Order).filter(Order.id == ticket.order_id).first()
        item = db.query(OrderItem).filter(OrderItem.id == ticket.order_item_id).first()
        seat_label = None
        if ticket.seat_id:
            seat = db.query(Seat).filter(Seat.id == ticket.seat_id).first()
            seat_label = seat.label if seat else None
        out.update(
            kind="order",
            name=order.buyer_name if order else None,
            ticket_type_name=(item.ticket_type_name if item else None),
            seat_label=seat_label or (item.section_label if item else None),
        )
    return out


@router.post("/{code}", response_model=CheckInResult)
def check_in(
    event_id: str,
    code: str,
    day: Optional[str] = None,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    """`day` is the door's local date (the scanner sends it); when
    absent we fall back to the server's UTC date. Undated tickets —
    everything pre-multi-day, and comps without a visit date — admit on
    any day, so single-day events behave exactly as before."""
    scan_day = (day or "").strip() or datetime.now(timezone.utc).date().isoformat()
    ticket = (
        db.query(Ticket)
        .filter(Ticket.event_id == event_id, Ticket.code == code.strip().upper())
        .with_for_update()
        .first()
    )
    if not ticket:
        return CheckInResult(result="not_found", code=code.strip().upper())
    info = _describe(db, ticket)
    if ticket.status == TicketStatus.REFUNDED:
        return CheckInResult(result="refunded", **info)
    if ticket.valid_date and ticket.valid_date != scan_day:
        # Dated code on the wrong day: NOT consumed — it still admits on
        # its own day.
        return CheckInResult(result="wrong_day", **info)
    if ticket.checked_in_at is not None:
        return CheckInResult(result="already_checked_in", checked_in_at=ticket.checked_in_at, **info)
    ticket.checked_in_at = datetime.now(timezone.utc)
    db.commit()
    return CheckInResult(result="admitted", checked_in_at=ticket.checked_in_at, **info)


@router.get("/stats", response_model=CheckInStats)
def check_in_stats(
    event_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    total_valid = (
        db.query(Ticket).filter(Ticket.event_id == event_id, Ticket.status == TicketStatus.VALID).count()
    )
    checked = (
        db.query(Ticket)
        .filter(Ticket.event_id == event_id, Ticket.checked_in_at.isnot(None))
        .count()
    )
    recent_rows = (
        db.query(Ticket)
        .filter(Ticket.event_id == event_id, Ticket.checked_in_at.isnot(None))
        .order_by(Ticket.checked_in_at.desc())
        .limit(5)
        .all()
    )
    recent = [
        CheckInResult(result="admitted", checked_in_at=t.checked_in_at, **_describe(db, t))
        for t in recent_rows
    ]
    return CheckInStats(total_valid=total_valid, checked_in=checked, recent=recent)