import enum
import uuid

from sqlalchemy import Boolean, Column, DateTime, Enum as SAEnum, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class SaleSource(str, enum.Enum):
    CSV_UPLOAD = "csv_upload"
    LIVE_API = "live_api"  # not produced by anything yet — reserved for a future platform integration
    NATIVE = "native"  # not produced by anything yet — reserved for native EventNXT ticket sales


class Sale(Base):
    """
    One reconciled ticket sale — reconciled meaning "we found out about it
    after the fact," not processed by EventNXT (this app deliberately
    doesn't handle payments). Every ingestion path (today: CSV upload;
    later: a live platform integration, or native sales if that's ever
    built) produces the same normalized row here, which is what lets the
    promo-code attribution and reward calculation stay identical
    regardless of where the sale data came from — see
    app/services/sales.py.

    promo_code_id is null when the sale used no code (or an unrecognized
    one) — still logged, just not attributed to a referrer.
    external_transaction_id, when the source data provides one, is what
    prevents re-uploading the same export from double-counting a sale;
    see app/services/sales.py's dedup logic.
    """

    __tablename__ = "sales"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    promo_code_id = Column(UUID(as_uuid=True), ForeignKey("promo_codes.id"), nullable=True)
    referral_contact_id = Column(UUID(as_uuid=True), ForeignKey("referral_contacts.id"), nullable=True)  # 0044
    # 0049: THE match, stamped at import by services/sale_matching —
    # which room this sale consumed and on which night. What seating
    # math counts; NULL on pre-0049 rows (normalized-name fallback) and
    # on non-admission rows. SET NULL on pool delete: sales outlive rooms.
    seating_category_id = Column(
        UUID(as_uuid=True), ForeignKey("seating_categories.id", ondelete="SET NULL"), nullable=True, index=True
    )
    event_day = Column(String, nullable=True)  # ISO date — the show night, NOT the purchase date
    is_admission = Column(Boolean, nullable=False, default=True, server_default="true")  # false: drink coupons etc.
    # 0050: a package/pass sold outside — consumes a head EVERY night.
    # Stamped to the family's BASE pool, event_day NULL; counted into
    # every family member by imported_heads_for_pool.
    all_days = Column(Boolean, nullable=False, default=False, server_default="false")

    buyer_name = Column(String, nullable=True)
    buyer_email = Column(String, nullable=True)
    amount = Column(Numeric, nullable=True)  # needed to compute a PERCENTAGE reward; optional otherwise
    ticket_type = Column(String, nullable=True)  # free text, matched (case-insensitively) against a POINTS
    # code's per-ticket-type earning rates — see PromoCodePointsRate — and
    # also against seating category names for the Seating Summary view
    quantity = Column(Integer, nullable=False, default=1)  # a single box-office transaction can cover
    # multiple tickets at once (a bulk purchase) — this is what a POINTS
    # reward multiplies by, so a 50-ticket sale correctly earns 50x the
    # per-ticket rate rather than being undercounted as one ticket
    sale_date = Column(String, nullable=True)  # ISO date string, consistent with Guest.visit_date elsewhere
    external_transaction_id = Column(String, nullable=True, index=True)

    source = Column(
        SAEnum(SaleSource, name="sale_source", values_callable=lambda e: [x.value for x in e]),
        nullable=False,
        default=SaleSource.CSV_UPLOAD,
    )
    computed_reward = Column(Numeric, nullable=True)  # snapshotted at import time from the matched code's terms

    imported_at = Column(DateTime(timezone=True), server_default=func.now())