# eventnxt-backend: app/services/comp_tickets.py
"""
Comp (guest-list) tickets: the same tickets table, codes, and door scan
as paid admissions — minted from a Guest instead of an Order. Also home
to guest-mode derivation and the comp-delivery lookup, since the three
travel together everywhere comps are handled.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.event_profile import EventProfile
from app.models.event_settings import EventSettings
from app.models.guest import Guest
from app.models.guest_type import GuestType
from app.models.ticket import Ticket, TicketStatus
from app.services.ticketing import generate_ticket_code
from app.config import settings as app_settings

GUEST_MODES = ("invite", "distribute", "select")


def effective_guest_mode(db: Session, guest: Guest, allotment: dict | None = None) -> str:
    """
    The guest's actual experience:
      1. the guest's own guest_mode, if set (per-guest override)
      2. else the guest type's guest_mode, if set
      3. else derived the legacy way — allotment holders distribute,
         everyone else is a plain invite.
    Callers that already computed effective_allotment pass it in to
    avoid a second lookup.
    """
    if guest.guest_mode in GUEST_MODES:
        return guest.guest_mode
    if guest.guest_type_id:
        gt = db.query(GuestType).filter(GuestType.id == guest.guest_type_id).first()
        if gt and gt.guest_mode in GUEST_MODES:
            return gt.guest_mode
    if allotment is None:
        from app.services import seating

        allotment = seating.effective_allotment(db, guest)
    from app.services import seating

    return "distribute" if seating.is_allotment_holder(allotment) else "invite"


def get_event_settings_row(db: Session, event_id) -> EventSettings | None:
    """Direct read for public/unauthenticated paths (the RSVP page has no
    org token to run the settings endpoint's get-or-create)."""
    return db.query(EventSettings).filter(EventSettings.event_id == event_id).first()


def comp_delivery_for(db: Session, event_id) -> str:
    row = get_event_settings_row(db, event_id)
    return row.comp_delivery if row else "rsvp_required"


def is_native_ticketing(db: Session, event_id) -> bool:
    """Comp ticket codes only mint for events selling through EventNXT —
    the rule as stated: 'if selling thru eventnxt then when RSVP is yes,
    tickets are auto generated and sent.' No settings row = never
    explicitly chosen = infer native only if native ticket types exist
    (mirrors the settings endpoint's inference without needing auth)."""
    row = get_event_settings_row(db, event_id)
    if row:
        return row.ticketing_mode == "native"
    from app.models.ticket_type import TicketType

    return db.query(TicketType.id).filter(TicketType.event_id == event_id).limit(1).first() is not None


def valid_comp_tickets(db: Session, guest: Guest) -> list[Ticket]:
    return (
        db.query(Ticket)
        .filter(Ticket.guest_id == guest.id, Ticket.status == TicketStatus.VALID)
        .order_by(Ticket.created_at)
        .all()
    )


def issue_comp_tickets(db: Session, guest: Guest) -> list[Ticket]:
    """
    Bring the guest's VALID comp tickets up to party_size — idempotent, so
    calling it from both the auto-send path and the RSVP-confirm path can
    never double-mint (it only tops up the shortfall). Caller commits.
    """
    existing = valid_comp_tickets(db, guest)
    shortfall = max(0, (guest.party_size or 1) - len(existing))
    minted = []
    for _ in range(shortfall):
        t = Ticket(
            guest_id=guest.id,
            event_id=guest.event_id,
            code=generate_ticket_code(),
            status=TicketStatus.VALID,
        )
        db.add(t)
        minted.append(t)
    # Hand-placed guests (reserved seats assigned before the RSVP came
    # back): stamp their seats onto the new codes so the confirmation
    # email, order page, and door scan all show "Section A · Seat 3".
    from app.services import seats as seats_service

    if minted:
        db.flush()  # tickets need identities before stamping
    seats_service.restamp_guest_tickets(db, guest)
    return existing + minted


def send_comp_ticket_email(db: Session, guest: Guest, tickets: list[Ticket]) -> bool:
    """
    Email the guest their admission code(s). Best-effort: a failed or
    unconfigured send never blocks the RSVP — the tickets exist either
    way and the organizer can re-send from the dashboard. Returns whether
    an email actually went out.
    """
    if not guest.email or not tickets:
        return False

    profile = db.query(EventProfile).filter(EventProfile.event_id == guest.event_id).first()
    event_name = profile.title if profile else "your event"

    # Assigned seats (hand-placed guests): show the seat next to its code.
    from app.models.seat import Seat

    seat_ids = [t.seat_id for t in tickets if t.seat_id]
    seat_by_id = (
        {s.id: s.label for s in db.query(Seat).filter(Seat.id.in_(seat_ids)).all()} if seat_ids else {}
    )
    seat_for = lambda t: seat_by_id.get(t.seat_id)  # noqa: E731

    codes = "\n".join(
        f"  {t.code}" + (f"  ({seat_for(t)})" if seat_for(t) else "") for t in tickets
    )
    plural = "s" if len(tickets) > 1 else ""
    when = f"\nDate: {guest.visit_date}" if guest.visit_date else ""
    page = f"\nEvent page: {app_settings.eventnxt_frontend_url}/e/{profile.slug}" if profile and profile.is_published else ""

    text = (
        f"Hi {guest.name},\n\n"
        f"You're confirmed for {event_name}.{when}\n\n"
        f"Your admission code{plural} — show at the door:\n{codes}\n"
        f"{page}\n\n"
        f"See you there!"
    )

    qr_base = f"{app_settings.eventnxt_backend_url}/public/tickets"
    code_cells = "".join(
        f"<tr><td style='padding:10px 12px;text-align:center'>"
        f"<img src='{qr_base}/{t.code}/qr.png' width='150' height='150' "
        f"style='display:block;margin:0 auto 6px;border-radius:8px' alt='QR for {t.code}'/>"
        f"<span style='font-family:monospace;font-size:15px'>{t.code}</span>"
        + (f"<br/><span style='font-size:13px'>{seat_for(t)}</span>" if seat_for(t) else "")
        + f"</td></tr>"
        for t in tickets
    )
    html = (
        f"<p>Hi {guest.name},</p>"
        f"<p>You're confirmed for <strong>{event_name}</strong>.{(' Date: ' + str(guest.visit_date)) if guest.visit_date else ''}</p>"
        f"<p>Your admission code{plural} — show at the door (scannable or typed):</p>"
        f"<table>{code_cells}</table>"
        f"<p>See you there!</p>"
    )

    try:
        from app.services.email import send_email

        send_email(to=guest.email, subject=f"Your ticket{plural} for {event_name}", text_body=text, html_body=html)
        return True
    except Exception:
        return False


def mark_resolved(request, status: str) -> None:
    request.status = status
    request.resolved_at = datetime.now(timezone.utc)