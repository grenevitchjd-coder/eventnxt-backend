"""eventnxt-backend: app/services/referrals.py

Per-recipient outreach attribution (migration 0044) — the shared rules
every channel uses, so native checkout, the $0 instant path, and CSV
import can never disagree about who gets credit.
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.referral_contact import ReferralContact
from app.services.lookups import ci_equals


def single_match_contact(db: Session, event_id, buyer_email: str):
    """
    The email-match fallback for buyers who clicked on one device and
    bought on another: credit the sender ONLY when exactly one contact
    row in this event matches the buyer's email. Two matches — even two
    codes of the same referrer — is ambiguity, and guessing between two
    people's money is worse than admitting it: return None and the sale
    stays unattributed for the organizer to see.
    """
    if not buyer_email:
        return None
    matches = (
        db.query(ReferralContact)
        .filter(ReferralContact.event_id == event_id, ci_equals(ReferralContact.email, buyer_email.strip()))
        .limit(2)
        .all()
    )
    return matches[0] if len(matches) == 1 else None


def stamp_click(db: Session, event_id, contact_token: str) -> None:
    """First tracked landing wins; later clicks don't move the stamp."""
    if not contact_token:
        return
    contact = (
        db.query(ReferralContact)
        .filter(ReferralContact.event_id == event_id, ReferralContact.token == contact_token)
        .first()
    )
    if contact and contact.clicked_at is None:
        contact.clicked_at = datetime.now(timezone.utc)