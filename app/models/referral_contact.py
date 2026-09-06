"""eventnxt-backend: app/models/referral_contact.py"""
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class ReferralContact(Base):
    """
    One person a referrer entered on their portal's "refer people" tab
    (migration 0044). The token makes their invite email's link unique —
    /e/<slug>?ref=<CODE>&r=<token> — so clicks and purchases trace to
    THIS person, not just the code. A contact is scoped to one promo
    code: the same buyer invited by two referrers is two rows, and the
    single-match email fallback deliberately refuses to pick between
    them (the locked attribution policy).
    """

    __tablename__ = "referral_contacts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    promo_code_id = Column(UUID(as_uuid=True), ForeignKey("promo_codes.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    email = Column(String, nullable=False, index=True)
    token = Column(String, nullable=False, unique=True, index=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    clicked_at = Column(DateTime(timezone=True), nullable=True)  # first tracked landing
    created_at = Column(DateTime(timezone=True), server_default=func.now())