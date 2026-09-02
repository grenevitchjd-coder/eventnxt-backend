# eventnxt-backend: app/routers/rsvp.py
import secrets

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.guest import Guest, GuestAllocationStatus
from app.models.guest_type import GuestType
from app.models.promo_code import PromoCode, RewardType
from app.models.reward_redemption import RewardRedemption
from app.models.guest_ticket_request import GuestTicketRequest
from app.schemas.rsvp import (
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
from app.services import seating
from app.services import comp_tickets

router = APIRouter(tags=["rsvp"])


def _get_guest_by_token_or_404(db: Session, token: str) -> Guest:
    guest = db.query(Guest).filter(Guest.rsvp_token == token).first()
    if not guest:
        raise HTTPException(status_code=404, detail="That RSVP link isn't valid.")
    return guest


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

    result = []
    for code in codes:
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
                points_available=points_available,
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
    latest_req = (
        db.query(GuestTicketRequest)
        .filter(GuestTicketRequest.guest_id == guest.id)
        .order_by(GuestTicketRequest.created_at.desc())
        .first()
    )
    mode = comp_tickets.effective_guest_mode(db, guest, allotment)
    available_days = sorted(allotment.keys()) if mode == "select" else None
    return {
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
            guest_type_name=guest_type.name,
            allocation_status=guest.allocation_status.value,
            visit_date=guest.visit_date,
            party_size=guest.party_size,
            is_allotment_holder=False,
            referral_codes=_build_referral_codes(db, str(guest.event_id), str(guest.id)),
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
        guest_type_name=guest_type.name,
        allocation_status=guest.allocation_status.value,
        visit_date=guest.visit_date,
        party_size=guest.party_size,
        is_allotment_holder=True,
        day_allotments=day_allotments,
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
        referral_codes=_build_referral_codes(db, str(guest.event_id), str(guest.id)),
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

        new_category_id, new_section_label = seating.resolve_seating_placement(
            db, str(guest.event_id), str(guest.guest_type_id), party_size=guest.party_size, visit_date=guest.visit_date
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
        # A declined guest releases their seat back to the pool.
        guest.seating_category_id = None
        guest.section_label = None
        # ...and any hand-assigned seats release from the guest but STAY
        # reserved, ready to hand to someone else.
        from app.services import seats as seats_service

        for seat in seats_service.guest_seats(db, guest.id):
            seat.guest_id = None
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
    db.add(GuestTicketRequest(guest_id=guest.id, quantity=payload.quantity, note=payload.note or None))
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
                # ticket_allotment_overridden doesn't actually matter here
                # since effective_allotment() always returns {} for any
                # guest with allocated_by_guest_id set — but leaving it
                # False (the default) is correct either way: this
                # recipient never distributes further.
                rsvp_token=secrets.token_urlsafe(24),
            )
        )

    db.commit()
    return get_rsvp_info(token, db)


@router.post("/public/rsvp/{token}/redeem", response_model=RSVPInfoResponse)
def redeem_reward(token: str, payload: RSVPRedeemRequest, db: Session = Depends(get_db)):
    """
    A referrer claiming a reward at a redemption tier for one of their
    own promo codes. Verifies the code actually belongs to this guest
    before touching anything — without that check, a crafted request
    could redeem against a code that isn't the caller's own.
    """
    guest = _get_guest_by_token_or_404(db, token)

    code = db.query(PromoCode).filter(PromoCode.id == payload.promo_code_id).first()
    if not code or str(code.guest_id) != str(guest.id):
        raise HTTPException(status_code=404, detail="That promo code isn't associated with this link.")

    redemptions_service.redeem(
        db, str(guest.event_id), payload.promo_code_id, payload.redemption_tier_id, payload.choice
    )
    return get_rsvp_info(token, db)