import enum
import uuid

from sqlalchemy import Column, DateTime, Enum as SAEnum, ForeignKey, Numeric, String
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

    buyer_name = Column(String, nullable=True)
    buyer_email = Column(String, nullable=True)
    amount = Column(Numeric, nullable=True)  # needed to compute a PERCENTAGE reward; optional otherwise
    ticket_type = Column(String, nullable=True)  # free text, matched (case-insensitively) against a POINTS
    # code's per-ticket-type earning rates — see PromoCodePointsRate
    sale_date = Column(String, nullable=True)  # ISO date string, consistent with Guest.visit_date elsewhere
    external_transaction_id = Column(String, nullable=True, index=True)

    source = Column(
        SAEnum(SaleSource, name="sale_source", values_callable=lambda e: [x.value for x in e]),
        nullable=False,
        default=SaleSource.CSV_UPLOAD,
    )
    computed_reward = Column(Numeric, nullable=True)  # snapshotted at import time from the matched code's terms

    imported_at = Column(DateTime(timezone=True), server_default=func.now())