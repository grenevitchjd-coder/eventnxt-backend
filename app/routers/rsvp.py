# eventnxt-backend: app/routers/rsvp.py
import secrets

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import settings as app_settings
from app.database import get_db
from app.models.ticket import Ticket, TicketStatus
from app.models.guest import Guest, GuestAllocationStatus
from app.models.guest_type import GuestType
from app.models.promo_code import PromoCode, RewardType
from app.models.reward_redemption import RewardRedemption
from app.models.guest_ticket_request import GuestTicketRequest
from app.models.seating_category import SeatingCategory
from app.schemas.rsvp import (
    PayoutTermsAcceptRequest,
    DealBonusTier,
    DealPointsRate,
    ReferralContactInfo,
    RSVPReferRequest,
    DayGrantItem,
    DayAllotment,
    DistributedRecipient,
    EligibleTier,
    RedemptionHistoryItem,
    ReferralCodeInfo,
    RSVPDistributeRequest,
    RSVPInfoResponse,
    RSVPRedeemRequest,
    RSVPRespondRequest,
    RSVPTicketRequestCreate,
)
from app.services import redemptions as redemptions_service
from app.services import sales as sales_service
from app.models.promo_code_points_rate import PromoCodePointsRate
from app.services import bonuses as bonuses_service
import secrets
from datetime import datetime, timezone

from app.models.event_profile import EventProfile
from app.models.referral_contact import ReferralContact
from app.models.sale import Sale
from app.services import email as email_service
from app.config import settings
from app.services import seating
from app.services import comp_tickets

router = APIRouter(tags=["rsvp"])


def _get_guest_by_token_or_404(db: Session, token: str) -> Guest:
    guest = db.query(Guest).filter(Guest.rsvp_token == token).first()
    if not guest:
        raise HTTPException(status_code=404, detail="That RSVP link isn't valid.")
    return guest


def _payout_terms_pending(db: Session, guest: Guest) -> bool:
    """0052: this guest holds referral codes but hasn't signed the
    payout-terms wall. While True, the payload withholds their codes
    and /refer + /redeem refuse — nothing referral-facing exists."""
    if guest.payout_terms_accepted_at is not None:
        return False
    return (
        db.query(PromoCode.id)
        .filter(PromoCode.guest_id == guest.id)
        .first()
        is not None
    )


def _referral_codes_if_signed(db: Session, guest: Guest):
    """(codes, payout_terms_required) — WITHHELD until the wall is signed."""
    if _payout_terms_pending(db, guest):
        return [], True
    return _build_referral_codes(db, str(guest.event_id), str(guest.id)), False


def _build_referral_codes(db: Session, event_id: str, guest_id: str):
    """
    Every promo code this guest holds, each with its points balance (for
    a points-type code), which redemption tiers it's currently eligible
    for, and its redemption history — independent of whether this guest
    is also a ticket-allotment holder, since a referrer might just be
    referring, not distributing tickets at all.
    """
    codes = db.query(PromoCode).filter(PromoCode.guest_id == guest_id).all()
    if not codes:
        return None

    agg = sales_service.sale_aggregates_by_code(db, event_id)
    contact_rows = (
        db.query(ReferralContact)
        .filter(ReferralContact.promo_code_id.in_([c.id for c in codes]))
        .order_by(ReferralContact.created_at)
        .all()
    )
    # Per-person conversion: what each invited person actually bought,
    # from the same shared Sale table everything else reads.
    conv = {}
    if contact_rows:
        from sqlalchemy import func as _f

        for cid, tix, amt in (
            db.query(Sale.referral_contact_id, _f.coalesce(_f.sum(Sale.quantity), 0), _f.coalesce(_f.sum(Sale.amount), 0))
            .filter(Sale.referral_contact_id.in_([c.id for c in contact_rows]))
            .group_by(Sale.referral_contact_id)
            .all()
        ):
            conv[cid] = {"tickets": int(tix), "amount": float(amt)}
    contacts_by_code = {}
    for ct in contact_rows:
        contacts_by_code.setdefault(ct.promo_code_id, []).append(
            ReferralContactInfo(
                name=ct.name,
                email=ct.email,
                sent_at=ct.sent_at,
                clicked=ct.clicked_at is not None,
                tickets_bought=conv.get(ct.id, {}).get("tickets", 0),
                amount_bought=conv.get(ct.id, {}).get("amount", 0),
            )
        )
    result = []
    for code in codes:
        a = agg.get(code.id)
        points_available = (
            redemptions_service.points_available(db, code.id) if code.reward_type == RewardType.POINTS else None
        )
        _, tiers = redemptions_service.eligible_tiers(db, event_id, code.id)
        redemptions = (
            db.query(RewardRedemption)
            .filter(RewardRedemption.promo_code_id == code.id)
            .order_by(RewardRedemption.redeemed_at.desc())
            .all()
        )
        result.append(
            ReferralCodeInfo(
                promo_code_id=str(code.id),
                code=code.code,
                reward_type=code.reward_type.value,
                reward_value=float(code.reward_value) if code.reward_value is not None else None,
                points_rates=[
                    DealPointsRate(ticket_type=pr.ticket_type, points=pr.points)
                    for pr in db.query(PromoCodePointsRate)
                    .filter(PromoCodePointsRate.promo_code_id == code.id)
                    .all()
                ] if code.reward_type == RewardType.POINTS else [],
                bonus_tiers=[
                    DealBonusTier(tickets_required=t.tickets_required, bonus_value=float(t.bonus_value))
                    for t in bonuses_service.effective_bonus_tiers(db, event_id, code)
                ],
                points_available=points_available,
                tickets_sold=int(a.tickets_sold) if a else 0,
                amount_sold=float(a.amount_sold) if a else 0,
                rows_missing_amount=a.rows_missing_amount if a else 0,
                link_clicks=code.link_clicks,
                total_reward=float(a.total_reward) if a and a.total_reward is not None else None,
                discount_type=code.discount_type,
                discount_value=float(code.discount_value) if code.discount_value is not None else None,
                referral_message_draft=code.referral_message_draft,
                contacts=contacts_by_code.get(code.id, []),
                eligible_tiers=[
                    EligibleTier(
                        redemption_tier_id=str(t["tier"].id),
                        points_required=t["tier"].points_required,
                        label=t["tier"].label,
                        cash_value=float(t["option"].cash_value) if t["option"].cash_value is not None else None,
                        ticket_value=t["option"].ticket_value,
                        affordable=t["affordable"],
                    )
                    for t in tiers
                ],
                redemption_history=[
                    RedemptionHistoryItem(
                        choice=r.choice.value,
                        points_spent=r.points_spent,
                        cash_value=float(r.cash_value) if r.cash_value is not None else None,
                        ticket_value=r.ticket_value,
                        payout_status=r.payout_status.value if r.payout_status else None,
                        redeemed_at=r.redeemed_at.isoformat(),
                    )
                    for r in redemptions
                ],
            )
        )
    return result


def _guest_extras(db: Session, guest: Guest, allotment: dict) -> dict:
    """The mode/needs-seating/ticket fields both RSVPInfoResponse shapes share."""
    codes = [t.code for t in comp_tickets.valid_comp_tickets(db, guest)]
    extras_outreach = {
        "outreach_terms_accepted_at": guest.outreach_terms_accepted_at,  # 0051
        "payout_terms_accepted_at": guest.payout_terms_accepted_at,  # 0052
    }
    latest_req = (
        db.query(GuestTicketRequest)
        .filter(GuestTicketRequest.guest_id == guest.id)
        .order_by(GuestTicketRequest.created_at.desc())
        .first()
    )
    mode = comp_tickets.effective_guest_mode(db, guest, allotment)
    spend_total = comp_tickets.effective_spend_total(db, guest, allotment, mode) if allotment else None
    cap_sum = sum(allotment.values()) if allotment else 0
    # Choose-within-caps is DATA, not a mode: whenever the total is
    # less than the sum of the day grants, the grants are ceilings and
    # the guest picks where to spend. (Legacy 'select' guests land here
    # automatically via their party-size total.)
    choose = bool(allotment) and spend_total is not None and spend_total < cap_sum
    available_days = sorted(allotment.keys()) if choose else None
    day_grants = (
        [DayGrantItem(date=d, quantity=q) for d, q in sorted(allotment.items())]
        if allotment and mode in ("invite", "select")
        else None
    )
    return {
        **extras_outreach,
        "day_grants": day_grants,
        "spend_total": spend_total if (choose or mode == "distribute") else None,
        "choose_within_caps": choose,
        "effective_mode": mode,
        "needs_seating": bool(guest.needs_seating),
        "available_days": available_days,
        "ticket_codes": codes or None,
        "ticket_request_status": latest_req.status if latest_req else None,
    }


@router.get("/public/rsvp/{token}", response_model=RSVPInfoResponse)
def get_rsvp_info(token: str, db: Session = Depends(get_db)):
    guest = _get_guest_by_token_or_404(db, token)
    guest_type = db.query(GuestType).filter(GuestType.id == guest.guest_type_id).first()

    allotment = seating.effective_allotment(db, guest)
    mode = comp_tickets.effective_guest_mode(db, guest, allotment)

    if mode != "distribute":
        return RSVPInfoResponse(
            guest_name=guest.name,
            guest_type_name=guest_type.name if guest_type else None,
            allocation_status=guest.allocation_status.value,
            visit_date=guest.visit_date,
            party_size=guest.party_size,
            is_allotment_holder=False,
            referral_codes=_referral_codes_if_signed(db, guest)[0],
            payout_terms_required=_referral_codes_if_signed(db, guest)[1],
            **_guest_extras(db, guest, allotment),
        )

    children = db.query(Guest).filter(Guest.allocated_by_guest_id == guest.id).all()
    distributed_by_day = {}
    for c in children:
        if c.visit_date:
            distributed_by_day[c.visit_date] = distributed_by_day.get(c.visit_date, 0) + c.party_size

    day_allotments = [
        DayAllotment(
            date=date,
            total=total,
            distributed=distributed_by_day.get(date, 0),
            remaining=max(total - distributed_by_day.get(date, 0), 0),
        )
        for date, total in sorted(allotment.items())
    ]

    return RSVPInfoResponse(
        guest_name=guest.name,
        guest_type_name=guest_type.name if guest_type else None,
        allocation_status=guest.allocation_status.value,
        visit_date=guest.visit_date,
        party_size=guest.party_size,
        is_allotment_holder=True,
        day_allotments=day_allotments,
        distributed_recipients=[
            DistributedRecipient(
                id=str(c.id),
                name=c.name,
                email=c.email,
                visit_date=c.visit_date,
                party_size=c.party_size,
                allocation_status=c.allocation_status.value,
                rsvp_confirmed=c.rsvp_confirmed,
                rsvp_link=f"{app_settings.eventnxt_frontend_url}/rsvp/{c.rsvp_token}",
            )
            for c in children
        ],
        referral_codes=_referral_codes_if_signed(db, guest)[0],
        payout_terms_required=_referral_codes_if_signed(db, guest)[1],
        **_guest_extras(db, guest, allotment),
    )


@router.post("/public/rsvp/{token}/respond", response_model=RSVPInfoResponse)
def respond_to_rsvp(token: str, payload: RSVPRespondRequest, db: Session = Depends(get_db)):
    """
    The simple confirm/decline path — for an ordinary guest, or a
    delegated recipient confirming the specific ticket someone gave them.
    Not for allotment holders — they use /distribute instead.
    """
    guest = _get_guest_by_token_or_404(db, token)
    allotment = seating.effective_allotment(db, guest)
    mode = comp_tickets.effective_guest_mode(db, guest, allotment)
    if mode == "distribute":
        raise HTTPException(
            status_code=400, detail="This link is for distributing tickets, not a simple RSVP — use /distribute."
        )

    if payload.attending and payload.day_quantities:
        # Per-day acceptance grid. Invite: reduce-only against the grant
        # ("need fewer" — more goes through /request-tickets). Select:
        # spread up to party_size across the offered days, capped per
        # day. Accepted shape replaces the guest's grant rows, voids any
        # excess already-minted codes, and mints the rest below.
        if not allotment:
            raise HTTPException(status_code=400, detail="This invitation has no per-day tickets to adjust.")
        cleaned: dict = {}
        for d, q in payload.day_quantities.items():
            if d not in allotment:
                raise HTTPException(status_code=400, detail=f'"{d}" isn\'t one of the days on this invitation.')
            try:
                q = int(q)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="Ticket counts must be whole numbers.")
            if q < 0:
                raise HTTPException(status_code=400, detail="Ticket counts can't be negative.")
            if q > allotment[d]:
                raise HTTPException(
                    status_code=400,
                    detail=f"That's more than your {allotment[d]} for {d} — use the request button to ask for extras.",
                )
            cleaned[d] = q
        accepted = {d: q for d, q in cleaned.items() if q > 0}
        spend_cap = comp_tickets.effective_spend_total(db, guest, allotment, mode)
        if spend_cap and spend_cap < sum(allotment.values()):
            total = sum(cleaned.values())
            if total > spend_cap:
                raise HTTPException(
                    status_code=400,
                    detail=f"You have {spend_cap} tickets to place — that adds up to {total}.",
                )
        if not accepted:
            raise HTTPException(status_code=400, detail="Pick at least one ticket, or decline instead.")
        from app.schemas.guest import TicketAllotmentDayItem

        seating.replace_guest_ticket_allotment(
            db, guest.id, [TicketAllotmentDayItem(date=d, quantity=q) for d, q in sorted(accepted.items())]
        )
        guest.ticket_allotment_overridden = True
        comp_tickets.shrink_guest_day_codes(db, guest, accepted)
        guest.visit_date = next(iter(accepted)) if (mode == "select" and len(accepted) == 1) else None

    if payload.attending:
        # 'select'-mode guests choose their own day; validate against the
        # guest type's allotment days when any are defined.
        if mode == "select" and payload.visit_date:
            if allotment and payload.visit_date not in allotment:
                raise HTTPException(
                    status_code=400,
                    detail=f'"{payload.visit_date}" isn\'t one of the valid dates for this invitation.',
                )
            guest.visit_date = payload.visit_date

        # Recipients confirm at distribution now — a stray click on their
        # link must not re-run placement (they'd count themselves as
        # occupying their section and hop to the next one). Their yes is
        # already recorded; just report state.
        if (
            guest.allocated_by_guest_id
            and guest.allocation_status == GuestAllocationStatus.CONFIRMED
        ):
            return get_rsvp_info(token, db)

        parent_cohort = True
        if guest.allocated_by_guest_id:
            parent_cohort = bool(
                db.query(Guest.cohort_together).filter(Guest.id == guest.allocated_by_guest_id).scalar()
            )
        # A recipient's parent may have chosen a pool/section for their
        # whole allotment — that choice wins when it has room; otherwise
        # the ordinary type-priority walk takes over.
        new_category_id, new_section_label = (None, None)
        if guest.allocated_by_guest_id:
            _parent = db.query(Guest).filter(Guest.id == guest.allocated_by_guest_id).first()
            new_category_id, new_section_label = seating.resolve_parent_override(
                db, _parent, guest.party_size, guest.visit_date
            )
        if new_category_id is None:
            new_category_id, new_section_label = seating.resolve_seating_placement(
                db, str(guest.event_id), str(guest.guest_type_id), party_size=guest.party_size,
                visit_date=guest.visit_date,
                allocated_by_guest_id=guest.allocated_by_guest_id, cohort_together=parent_cohort,
            )
        # Hand-placed guests (organizer assigned them specific reserved
        # seats) are NEVER moved by the priority resolver — their yes
        # confirms them exactly where they were placed.
        from app.services import seats as seats_service

        if seats_service.guest_seats(db, guest.id):
            guest.allocation_status = GuestAllocationStatus.CONFIRMED
            guest.rsvp_confirmed = "yes"
            guest.needs_seating = False
            if comp_tickets.is_native_ticketing(db, guest.event_id):
                tickets = comp_tickets.issue_comp_tickets(db, guest)
                db.flush()
                comp_tickets.send_comp_ticket_email(db, guest, tickets)
        elif guest.seating_category_id:
            # An organizer-preset area/section (e.g. picked from the
            # Invites grid's Seating dropdown while the guest was still
            # PENDING) is a deliberate choice too — it should stick
            # regardless of how/when the guest confirms, not get
            # silently overridden by the type's priority walk the
            # instant they click yes. Found live 2026-09: dropdown set
            # to "Row 3 Preferred Seating", RSVP yes flipped it back to
            # the type's own priority target ("Row 1") because only an
            # actual assigned SEAT was protected before this, not a
            # bare category/section choice. Still capacity-checked —
            # room may have changed since it was set — with the same
            # soft needs-seating landing as an unresolvable priority
            # walk, never a lost yes.
            try:
                seating.check_capacity(
                    db, str(guest.event_id), str(guest.seating_category_id),
                    party_size=guest.party_size, exclude_guest_id=guest.id,
                )
                if guest.section_label:
                    seating.check_section_capacity(
                        db, str(guest.event_id), str(guest.seating_category_id), guest.section_label,
                        party_size=guest.party_size, exclude_guest_id=guest.id,
                    )
                guest.allocation_status = GuestAllocationStatus.CONFIRMED
                guest.rsvp_confirmed = "yes"
                guest.needs_seating = False
                if comp_tickets.is_native_ticketing(db, guest.event_id):
                    tickets = comp_tickets.issue_comp_tickets(db, guest)
                    db.flush()
                    comp_tickets.send_comp_ticket_email(db, guest, tickets)
            except HTTPException:
                guest.rsvp_confirmed = "yes"
                guest.needs_seating = True
        elif new_category_id is None and seating.has_seating_priorities(db, str(guest.guest_type_id)):
            # SOFT landing, not a hard error: the yes is recorded, no seat
            # is claimed (allocation stays PENDING so capacity math never
            # counts a phantom), and the guest lands in the organizer's
            # Needs-seating queue. A hard "no room" to a comp guest —
            # often a sponsor or VIP — loses their yes entirely.
            guest.rsvp_confirmed = "yes"
            guest.needs_seating = True
        else:
            guest.seating_category_id = new_category_id
            guest.section_label = new_section_label
            guest.allocation_status = GuestAllocationStatus.CONFIRMED
            guest.rsvp_confirmed = "yes"
            guest.needs_seating = False
            # A comp landing in a SEAT-GRAIN area needs a REAL seat, not
            # just a section name — the priority walk is deliberately
            # grain-agnostic and stops at (category, section). A MULTI-
            # day guest needs the SAME seat identity across every one of
            # their nights' sibling pools, not just this one (2026-09 fix
            # — night one got a real seat, every other night fell back
            # to a bare section).
            _cat = db.query(SeatingCategory).filter(SeatingCategory.id == new_category_id).first()
            if _cat and _cat.sales_grain == "seat":
                # effective_allotment is the REAL source of a guest's
                # per-day quantities (own override rows only if set,
                # otherwise the guest type's defaults) — querying
                # GuestTicketAllotment directly missed every guest
                # relying on type defaults, since they carry no override
                # rows at all (found live 2026-09, same gap as guests.py
                # create_guest and the seat-days endpoint).
                days = [d for d, q in seating.effective_allotment(db, guest).items() if q > 0] or (
                    [guest.visit_date] if guest.visit_date else []
                )
                pool_ids = seating.sibling_pool_ids_for_days(db, new_category_id, days)
                picked = seats_service.pick_available_seat_family(db, pool_ids, new_section_label, guest.party_size)
                if picked:
                    seats_service.assign_guest_seats(db, guest=guest, seat_ids=picked)
            # Selling through EventNXT: a confirmed yes mints and emails
            # real admission codes (same table and format as paid tickets,
            # so Find My Tickets and future QR check-in treat comps
            # identically). Idempotent — auto-send may have minted already.
            if comp_tickets.is_native_ticketing(db, guest.event_id):
                tickets = comp_tickets.issue_comp_tickets(db, guest)
                db.flush()
                comp_tickets.send_comp_ticket_email(db, guest, tickets)
    else:
        guest.allocation_status = GuestAllocationStatus.DECLINED
        guest.rsvp_confirmed = "no"
        guest.needs_seating = False
        # A declined guest gives up their seat for good — releases back
        # to general availability automatically, same "gone means gone"
        # rule as deleting a guest (2026-09).
        guest.seating_category_id = None
        guest.section_label = None
        from app.services import seats as seats_service

        seats_service.release_seats_for_guest(db, guest.id)
        seats_service.restamp_guest_tickets(db, guest)

    db.commit()
    db.refresh(guest)
    return get_rsvp_info(token, db)


@router.post("/public/rsvp/{token}/request-tickets", response_model=RSVPInfoResponse)
def request_more_tickets(token: str, payload: RSVPTicketRequestCreate, db: Session = Depends(get_db)):
    """
    An invite/select guest asking the organizer for more tickets. One
    open request at a time — a pending one must be resolved before the
    next. Shows up in the Guests tab, where the capacity picture lives.
    """
    guest = _get_guest_by_token_or_404(db, token)
    allotment = seating.effective_allotment(db, guest)
    mode = comp_tickets.effective_guest_mode(db, guest, allotment)
    if mode == "distribute":
        raise HTTPException(
            status_code=400,
            detail="This link distributes an allotment — add recipients instead of requesting tickets.",
        )
    pending = (
        db.query(GuestTicketRequest)
        .filter(GuestTicketRequest.guest_id == guest.id, GuestTicketRequest.status == "pending")
        .first()
    )
    if pending:
        raise HTTPException(status_code=400, detail="You already have a request waiting for the organizer.")
    req_date = (payload.date or "").strip() or None
    if req_date:
        allot = seating.effective_allotment(db, guest)
        if allot and req_date not in allot:
            raise HTTPException(status_code=400, detail=f'"{req_date}" isn\'t one of the days on this invitation.')
    db.add(GuestTicketRequest(guest_id=guest.id, quantity=payload.quantity, date=req_date, note=payload.note or None))
    db.commit()
    return get_rsvp_info(token, db)


@router.post("/public/rsvp/{token}/distribute", response_model=RSVPInfoResponse)
def distribute_tickets(token: str, payload: RSVPDistributeRequest, db: Session = Depends(get_db)):
    """
    An allotment holder (model, sponsor) naming who gets their tickets.
    Only checks the TICKET COUNT limit here, per day — each named
    recipient still gets their own RSVP link and personally confirms via
    /respond, which is when their actual seat is resolved and checked.
    This mirrors how an organizer-added guest starts pending and only
    consumes a seat once confirmed — nothing here holds a seat until the
    recipient says yes.
    """
    guest = _get_guest_by_token_or_404(db, token)
    allotment = seating.effective_allotment(db, guest)
    if comp_tickets.effective_guest_mode(db, guest, allotment) != "distribute":
        raise HTTPException(status_code=400, detail="This link doesn't have tickets to distribute.")

    if not payload.recipients:
        raise HTTPException(status_code=400, detail="Add at least one recipient.")

    for r in payload.recipients:
        if r.visit_date not in allotment:
            raise HTTPException(
                status_code=400, detail=f'"{r.visit_date}" isn\'t one of the valid dates for these tickets.'
            )

    requested_by_day = {}
    for r in payload.recipients:
        requested_by_day[r.visit_date] = requested_by_day.get(r.visit_date, 0) + r.party_size
    seating.check_allotment_capacity_per_day(db, str(guest.id), requested_by_day, allotment)
    # Total budget (0039): day amounts are ceilings; when a TOTAL is set
    # lower than their sum, the whole allotment also caps at the total —
    # "25 across 10 Thu / 10 Fri / 10 Sat" hands out 25, never 30.
    if guest.spend_total is not None:
        already = seating.allotment_distributed_total(db, str(guest.id))
        wanted = sum(requested_by_day.values())
        if already + wanted > guest.spend_total:
            left = max(guest.spend_total - already, 0)
            raise HTTPException(
                status_code=400,
                detail=f"That's more than your total of {guest.spend_total} tickets — you have {left} left across all days.",
            )

    children = []
    for r in payload.recipients:
        children.append(
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
                # ticket_allotment_overridden doesn't actually matter here
                # since effective_allotment() always returns {} for any
                # guest with allocated_by_guest_id set — but leaving it
                # False (the default) is correct either way: this
                # recipient never distributes further.
                rsvp_token=secrets.token_urlsafe(24),
            )
        )
    for c in children:
        db.add(c)

    db.commit()

    # Recipients DON'T RSVP — the sponsor entering them IS the vouch
    # (2026-09-05). Each one is confirmed on the spot: placed (the
    # parent's explicit recipients-seating first, then the type's
    # priorities), minted, and emailed their actual tickets. When no
    # section has room, the soft-landing rule from the yes-path applies:
    # they stay pending with needs_seating so the organizer's queue
    # catches them and no phantom seat is counted.
    parent_cohort = bool(guest.cohort_together)
    from app.services import seats as seats_service

    for c in children:
        new_category_id, new_section_label = seating.resolve_parent_override(
            db, guest, c.party_size, c.visit_date
        )
        if new_category_id is None:
            new_category_id, new_section_label = seating.resolve_seating_placement(
                db, str(c.event_id), str(c.guest_type_id), party_size=c.party_size,
                visit_date=c.visit_date,
                allocated_by_guest_id=c.allocated_by_guest_id, cohort_together=parent_cohort,
            )
        if new_category_id is None and seating.has_seating_priorities(db, str(c.guest_type_id)):
            c.rsvp_confirmed = "yes"
            c.needs_seating = True
            continue
        c.seating_category_id = new_category_id
        c.section_label = new_section_label
        c.allocation_status = GuestAllocationStatus.CONFIRMED
        c.rsvp_confirmed = "yes"
        c.needs_seating = False
        # A comp landing in a SEAT-GRAIN area needs a REAL seat, not just
        # a section name — resolve_parent_override/resolve_seating_placement
        # are deliberately grain-agnostic and stop at (category, section).
        # FIRST draw from whatever the holder already reserved for "hold
        # now" (hold_seats_for_allotment) — that's the guaranteed
        # adjacent block sponsors are promised; only pick fresh from the
        # general pool for any shortfall (2026-09 fix). Flushed below
        # before the NEXT sibling picks, so two recipients never land on
        # the same seat.
        _cat = db.query(SeatingCategory).filter(SeatingCategory.id == new_category_id).first()
        if _cat and _cat.sales_grain == "seat":
            transferred = seats_service.take_held_seats(db, guest.id, new_category_id, new_section_label, c, c.party_size)
            remaining = c.party_size - len(transferred)
            if remaining > 0:
                # assign_guest_seats WHOLESALE-replaces — must pass the
                # transferred seats too, or they'd read as "released"
                # (current but not in the new list) and get freed again.
                picked = seats_service.pick_available_seats(db, new_category_id, new_section_label, remaining)
                if picked:
                    seats_service.assign_guest_seats(db, guest=c, seat_ids=transferred + picked)
        # Placements happen back-to-back in this one request — flush so
        # the NEXT sibling's room math sees this one (spread placement
        # was packing everyone into the same section without it).
        db.flush()
    db.commit()
    for c in children:
        db.refresh(c)
        if c.allocation_status == GuestAllocationStatus.CONFIRMED and comp_tickets.is_native_ticketing(db, c.event_id):
            tickets = comp_tickets.issue_comp_tickets(db, c)
            db.flush()
            comp_tickets.send_comp_ticket_email(db, c, tickets)
        else:
            # external ticketing or needs-seating: they still get the
            # heads-up email with their link (shows their status/tickets
            # once resolved) — nothing asks them to RSVP anymore.
            comp_tickets.send_recipient_invite_email(db, c, guest)
    db.commit()
    return get_rsvp_info(token, db)


@router.delete("/public/rsvp/{token}/recipients/{child_id}", response_model=RSVPInfoResponse)
def remove_distributed_recipient(token: str, child_id: str, db: Session = Depends(get_db)):
    """
    The distributor taking back a still-pending recipient — frees that
    day's budget for someone else. Only THEIR OWN pending recipients:
    once a recipient has confirmed (or holds valid codes), changes go
    through the organizer instead.
    """
    guest = _get_guest_by_token_or_404(db, token)
    child = (
        db.query(Guest)
        .filter(Guest.id == child_id, Guest.allocated_by_guest_id == guest.id)
        .first()
    )
    if not child:
        raise HTTPException(status_code=404, detail="That recipient isn't on this allocation.")
    # Recipients confirm at distribution now, so "already confirmed" can't
    # mean untouchable — the sponsor manages their own list. Removing one
    # voids any comp codes already issued (REFUNDED = invalid at the
    # door), frees their seat, and returns the budget. The one hard stop:
    # someone who has already walked through the door stays on the books.
    checked_in = (
        db.query(Ticket.id)
        .filter(Ticket.guest_id == child.id, Ticket.checked_in_at.isnot(None))
        .first()
    )
    if checked_in:
        raise HTTPException(
            status_code=400,
            detail=f"{child.name} has already checked in — ask the event organizer to make changes.",
        )
    # Void every code and detach the rows (guest_id is nullable exactly
    # for lineage like this) so the audit trail survives the delete.
    for t in db.query(Ticket).filter(Ticket.guest_id == child.id).all():
        t.status = TicketStatus.REFUNDED
        t.guest_id = None
    db.flush()
    db.delete(child)
    db.commit()
    return get_rsvp_info(token, db)


@router.post("/public/rsvp/{token}/accept-payout-terms", response_model=RSVPInfoResponse)
def accept_payout_terms(token: str, payload: PayoutTermsAcceptRequest, db: Session = Depends(get_db)):
    """
    0052: the payout-terms wall's submit — checkbox is client-side; what
    the server requires and RECORDS is the typed full legal name (the
    e-signature) plus the timestamp. COMMITTED immediately (acceptance
    is its own fact — 0051 lesson) and idempotent: re-signing updates
    nothing once stamped. The refreshed payload now includes the codes.
    """
    guest = _get_guest_by_token_or_404(db, token)
    name = " ".join(payload.legal_name.split())
    if len(name) < 3 or " " not in name:
        raise HTTPException(status_code=400, detail="Please enter your full legal name (first and last).")
    if guest.payout_terms_accepted_at is None:
        guest.payout_terms_accepted_at = datetime.now(timezone.utc)
        guest.payout_terms_legal_name = name
        db.commit()
    return get_rsvp_info(token, db)


@router.post("/public/rsvp/{token}/refer", response_model=RSVPInfoResponse)
def refer_people(token: str, payload: RSVPReferRequest, db: Session = Depends(get_db)):
    """
    The portal's "refer people" tab: the referrer enters names/emails
    and each person gets a unique tracked invite email —
    /e/<slug>?ref=<CODE>&r=<contact-token> — so clicks and purchases
    trace to that specific person. Verifies the code belongs to this
    token's guest (same ownership check as /redeem). Re-entering an
    email this code already invited RE-SENDS with the existing token
    (idempotent) rather than minting a second identity; the same email
    under a DIFFERENT referrer is deliberately allowed — that's the
    collision last-click-wins exists for. Requires a published event
    page: without a slug there is nothing to link to.
    """
    guest = _get_guest_by_token_or_404(db, token)
    if _payout_terms_pending(db, guest):
        raise HTTPException(status_code=400, detail="Accept the Referral Program Terms first.")
    # 0051: the Referral Outreach Policy gates EVERYTHING here — this
    # endpoint pipes referrer-written words through the platform's own
    # SMTP, so acceptance is enforced FIRST (before publish or code
    # checks), stamped once, and never asked again. Same shape as the
    # purchasing agreement's terms_accepted at checkout.
    if guest.outreach_terms_accepted_at is None:
        if not payload.outreach_terms_accepted:
            raise HTTPException(
                status_code=400,
                detail="Please accept the Referral Outreach Policy before sending invites.",
            )
        guest.outreach_terms_accepted_at = datetime.now(timezone.utc)
        # COMMIT, not flush: the acceptance is its own fact and must
        # survive whatever the rest of this request does (an unpublished
        # 400 must not silently un-accept the policy).
        db.commit()
    code = db.query(PromoCode).filter(PromoCode.id == payload.promo_code_id).first()
    if not code or str(code.guest_id) != str(guest.id):
        raise HTTPException(status_code=404, detail="That promo code isn't associated with this link.")
    profile = db.query(EventProfile).filter(EventProfile.event_id == guest.event_id).first()
    if not profile or not profile.is_published:
        raise HTTPException(status_code=400, detail="The event page isn't published yet — there's no link to send. Ask the organizer.")

    base = settings.eventnxt_frontend_url.rstrip("/")
    event_name = profile.title or "the event"
    sent = failed = 0
    for person in payload.contacts:
        contact = (
            db.query(ReferralContact)
            .filter(ReferralContact.promo_code_id == code.id, ReferralContact.email.ilike(person.email))
            .first()
        )
        if contact is None:
            contact = ReferralContact(
                event_id=guest.event_id,
                promo_code_id=code.id,
                name=person.name,
                email=person.email,
                token=secrets.token_urlsafe(16),
            )
            db.add(contact)
            db.flush()
        link = f"{base}/e/{profile.slug}?ref={code.code}&r={contact.token}"
        lines = [f"Hi {person.name},", ""]
        # Referrer's own words win; the organizer's draft is the prefill
        # they started from; the plain default covers neither existing.
        custom = (payload.message or "").strip()
        if custom:
            lines += [custom, ""]
        elif code.referral_message_draft:
            lines += [code.referral_message_draft, ""]
        else:
            lines += [f"{guest.name} thinks you'd love {event_name}.", ""]
        if code.discount_type == "percentage":
            lines.append(f"Use their link and get {code.discount_value}% off:")
        elif code.discount_type == "flat_amount":
            lines.append(f"Use their link and get ${code.discount_value} off:")
        else:
            lines.append("Get your tickets here:")
        lines += [link, "", f"Sent through EventNXT on behalf of {guest.name}."]
        try:
            email_service.send_email(
                to=person.email,
                subject=(payload.subject or "").strip() or f"{guest.name} invited you to {event_name}",
                text_body="\n".join(lines),
            )
            contact.sent_at = datetime.now(timezone.utc)
            sent += 1
        except Exception:
            failed += 1
    db.commit()
    info = get_rsvp_info(token, db)
    return info


@router.post("/public/rsvp/{token}/redeem", response_model=RSVPInfoResponse)
def redeem_reward(token: str, payload: RSVPRedeemRequest, db: Session = Depends(get_db)):
    """
    A referrer claiming a reward at a redemption tier for one of their
    own promo codes. Verifies the code actually belongs to this guest
    before touching anything — without that check, a crafted request
    could redeem against a code that isn't the caller's own.
    """
    guest = _get_guest_by_token_or_404(db, token)
    if _payout_terms_pending(db, guest):
        raise HTTPException(status_code=400, detail="Accept the Referral Program Terms first.")

    code = db.query(PromoCode).filter(PromoCode.id == payload.promo_code_id).first()
    if not code or str(code.guest_id) != str(guest.id):
        raise HTTPException(status_code=404, detail="That promo code isn't associated with this link.")

    redemptions_service.redeem(
        db, str(guest.event_id), payload.promo_code_id, payload.redemption_tier_id, payload.choice
    )
    return get_rsvp_info(token, db)