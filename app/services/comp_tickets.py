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
    # A delegated recipient (allocated_by set) is NEVER a distributor —
    # distribution is one level deep. Without this, a recipient sharing
    # their distributor's guest TYPE (the normal case) inherits the
    # type's 'distribute' mode and their own RSVP link stops working.
    if guest.allocated_by_guest_id is not None:
        own = guest.guest_mode if guest.guest_mode in ("invite", "select") else None
        return own or "invite"
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


def event_days_for(db: Session, event_id) -> list[str]:
    """
    The event's day list as ISO strings, inclusive — [] for single-day
    span or unconfigured days. Read from the settings row (no auth
    needed), so the public checkout path can mint dated codes.
    """
    from datetime import date, timedelta

    row = get_event_settings_row(db, event_id)
    if not row or row.ticket_span == "single_day" or not (row.first_day and row.last_day):
        return []
    try:
        first, last = date.fromisoformat(row.first_day), date.fromisoformat(row.last_day)
    except ValueError:
        return []
    days, d = [], first
    while d <= last and len(days) < 60:  # sanity cap
        days.append(d.isoformat())
        d += timedelta(days=1)
    return days


def shrink_guest_day_codes(db: Session, guest: Guest, accepted: dict) -> int:
    """
    A guest accepted FEWER tickets than granted: void each day's excess
    VALID dated codes beyond accepted[day] (days absent from `accepted`
    drop to zero). Seatless codes void first, newest first, so a
    hand-placed seat survives a shrink whenever the day keeps any
    tickets. Voided codes read REFUNDED — the door already shows those
    as DO-NOT-ADMIT, and derived availability frees any seat they held.
    Undated (legacy) codes are never touched. Returns how many voided.
    """
    dated = [t for t in valid_comp_tickets(db, guest) if t.valid_date]
    by_day: dict = {}
    for t in dated:
        by_day.setdefault(t.valid_date, []).append(t)
    voided = 0
    for day, tickets in by_day.items():
        keep = max(0, int(accepted.get(day, 0)))
        excess = len(tickets) - keep
        if excess <= 0:
            continue
        # void order: seatless first, then seated; newest first within each
        order = [t for t in tickets if t.seat_id is None][::-1] + [t for t in tickets if t.seat_id is not None][::-1]
        for t in order[:excess]:
            t.status = TicketStatus.REFUNDED
            voided += 1
    return voided


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
    party = guest.party_size or 1
    minted = []
    days = event_days_for(db, guest.event_id)
    has_undated = any(t.valid_date is None for t in existing)

    from app.services import seating

    allot = seating.effective_allotment(db, guest)
    mode = effective_guest_mode(db, guest, allot)
    if days and allot and mode in ("invite", "select") and not has_undated:
        # Invite with a per-day grant ("2 Thursday, 4 Saturday"): mint
        # exactly the granted quantity per granted day — dated codes,
        # idempotent per-day top-up, other days get nothing. Distributor
        # allotments never take this path (their allotment is a budget
        # for recipients, not tickets for themselves), and legacy
        # undated holders keep any-day codes untouched.
        by_day: dict = {}
        for t in existing:
            by_day[t.valid_date] = by_day.get(t.valid_date, 0) + 1
        for day, qty in sorted(allot.items()):
            if day not in days:
                continue  # a stale grant outside the event's days mints nothing
            for _ in range(max(0, qty - by_day.get(day, 0))):
                t = Ticket(
                    guest_id=guest.id,
                    event_id=guest.event_id,
                    code=generate_ticket_code(),
                    status=TicketStatus.VALID,
                    valid_date=day,
                )
                db.add(t)
                minted.append(t)
    elif days and not guest.visit_date and not has_undated:
        # Whole-event comp at a multi-day event: party_size dated codes
        # PER DAY — the guest walks in every day on that day's code,
        # same once-only door semantics as everything else. Idempotent
        # per day (tops up each day's shortfall only). Guests who
        # already hold undated codes are legacy — those admit any day,
        # so they're topped up the old way rather than doubled.
        by_day: dict = {}
        for t in existing:
            by_day[t.valid_date] = by_day.get(t.valid_date, 0) + 1
        for day in days:
            for _ in range(max(0, party - by_day.get(day, 0))):
                t = Ticket(
                    guest_id=guest.id,
                    event_id=guest.event_id,
                    code=generate_ticket_code(),
                    status=TicketStatus.VALID,
                    valid_date=day,
                )
                db.add(t)
                minted.append(t)
    else:
        shortfall = max(0, party - len(existing))
        for _ in range(shortfall):
            t = Ticket(
                guest_id=guest.id,
                event_id=guest.event_id,
                code=generate_ticket_code(),
                status=TicketStatus.VALID,
                # A day-specific guest's codes admit that day only;
                # legacy/undated comps admit any day.
                valid_date=guest.visit_date or None,
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


def sync_guest_tickets(db: Session, guest: Guest) -> tuple[int, int]:
    """
    Organizer's re-true: make the guest's VALID codes match their
    CURRENT shape in both directions — void what's no longer granted
    (day swaps, reductions) and mint what's missing (raises, new days).
    Returns (minted, voided).

    Target shape: per-day grant when they have one; else their visit
    day × party; else (multi-day event) party per event day. Single-day
    events manage the undated count to party_size. Legacy guests
    holding undated codes at a multi-day event are left alone on the
    void side — an undated code admits any day, so there's nothing
    day-shaped to reconcile; the top-up side still applies.
    """
    days = event_days_for(db, guest.event_id)
    from app.services import seating

    allot = seating.effective_allotment(db, guest)
    mode = effective_guest_mode(db, guest, allot)
    existing = valid_comp_tickets(db, guest)
    has_undated = any(t.valid_date is None for t in existing)
    voided = 0
    if days and not has_undated:
        if allot and mode in ("invite", "select"):
            target = {d: q for d, q in allot.items() if d in days}
        elif guest.visit_date:
            target = {guest.visit_date: guest.party_size or 1}
        else:
            target = {d: (guest.party_size or 1) for d in days}
        voided = shrink_guest_day_codes(db, guest, target)
        # autoflush is OFF: the voids above must land before the top-up
        # below re-queries VALID codes, or it counts ghosts.
        db.flush()
    elif not days:
        undated = [t for t in existing if t.valid_date is None]
        excess = len(undated) - (guest.party_size or 1)
        if excess > 0:
            order = [t for t in undated if t.seat_id is None][::-1] + [t for t in undated if t.seat_id is not None][::-1]
            for t in order[:excess]:
                t.status = TicketStatus.REFUNDED
                voided += 1
    db.flush()  # autoflush OFF — voids must land before the top-up counts
    before = len(valid_comp_tickets(db, guest))
    issue_comp_tickets(db, guest)
    minted = len(valid_comp_tickets(db, guest)) - before
    return minted, voided


def send_recipient_invite_email(db: Session, child: Guest, parent: Guest) -> bool:
    """
    Tell a distribution recipient they've been given tickets, with their
    own RSVP link to confirm. Best-effort like every send here — the
    child row exists either way and the link also shows on the parent's
    portal for manual forwarding.
    """
    if not child.email:
        return False
    profile = db.query(EventProfile).filter(EventProfile.event_id == child.event_id).first()
    event_name = profile.title if profile else "an event"
    link = f"{app_settings.eventnxt_frontend_url}/rsvp/{child.rsvp_token}"
    day = ""
    if child.visit_date:
        from datetime import date as _date

        try:
            day = f" for {_date.fromisoformat(child.visit_date).strftime('%a %b %-d')}"
        except ValueError:
            day = f" for {child.visit_date}"
    plural = "s" if (child.party_size or 1) > 1 else ""
    subject = f"{parent.name} sent you ticket{plural} to {event_name}"
    text = (
        f"Hi {child.name},\n\n"
        f"{parent.name} has given you {child.party_size or 1} ticket{plural}{day} to {event_name}.\n"
        f"Confirm here to receive your admission code{plural}:\n\n  {link}\n"
    )
    html = (
        f"<p>Hi {child.name},</p>"
        f"<p><strong>{parent.name}</strong> has given you <strong>{child.party_size or 1} ticket{plural}</strong>{day} to <strong>{event_name}</strong>.</p>"
        f"<p><a href='{link}' style='display:inline-block;padding:10px 18px;background:#0F6E56;color:#fff;border-radius:8px;text-decoration:none'>Confirm your ticket{plural}</a></p>"
        f"<p style='font-size:12px;color:#666'>{link}</p>"
    )
    from app.services.email import send_email

    try:
        send_email(child.email, subject, text, html)
        return True
    except Exception:
        return False


def send_comp_ticket_email(db: Session, guest: Guest, tickets: list[Ticket], note: str | None = None) -> bool:
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

    def day_for(t):
        if not t.valid_date:
            return None
        from datetime import date as _date

        try:
            return _date.fromisoformat(t.valid_date).strftime("%a %b %-d")
        except ValueError:
            return t.valid_date

    codes = "\n".join(
        f"  {t.code}"
        + (f"  [{day_for(t)}]" if day_for(t) else "")
        + (f"  ({seat_for(t)})" if seat_for(t) else "")
        for t in tickets
    )
    plural = "s" if len(tickets) > 1 else ""
    when = f"\nDate: {guest.visit_date}" if guest.visit_date else ""
    page = f"\nEvent page: {app_settings.eventnxt_frontend_url}/e/{profile.slug}" if profile and profile.is_published else ""

    section_note = (
        f"\nSeating: Section {guest.section_label}" if guest.section_label and not seat_ids else ""
    )
    note_text = f"\n\n{note.strip()}" if note and note.strip() else ""
    text = (
        f"Hi {guest.name},\n\n"
        f"You're confirmed for {event_name}.{when}{section_note}{note_text}\n\n"
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
        + (f"<br/><span style='font-size:13px;font-weight:600'>{day_for(t)}</span>" if day_for(t) else "")
        + (f"<br/><span style='font-size:13px'>{seat_for(t)}</span>" if seat_for(t) else "")
        + f"</td></tr>"
        for t in tickets
    )
    note_html = (
        f"<p style='background:#FDF6E3;border-left:4px solid #B4890A;padding:8px 12px'><strong>{note.strip()}</strong></p>"
        if note and note.strip()
        else ""
    )
    html = (
        f"<p>Hi {guest.name},</p>"
        + note_html
        +
        f"<p>You're confirmed for <strong>{event_name}</strong>.{(' Date: ' + str(guest.visit_date)) if guest.visit_date else ''}"
        f"{(' Seating: Section ' + guest.section_label + '.') if guest.section_label and not seat_ids else ''}</p>"
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