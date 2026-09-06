# eventnxt-backend: app/models/sale_type_mapping.py
import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class SaleTypeMapping(Base):
    """
    The organizer's saved answer to "which area is this ticket-type
    string?" (0049). raw_label is the NORMALIZED imported ticket-type
    text (trim / collapse whitespace / lowercase — the same
    normalization every family-discovery site uses), unique per event.
    Mapped once in import staging, applied automatically by
    services/sale_matching on every future upload — an alias table
    doing its job, exactly like tickets->partySize in the guest
    importer.

    seating_category_id: the BASE pool the label sells (day routing to
    "(MM/DD)" siblings happens at match time via pool_for_day, so one
    mapping covers every night). NULL + is_admission=True = "seen but
    not yet mapped" (imports proceed unstamped); is_admission=False =
    deliberately not a seat (drink coupon, merch) — imported and
    promo-attributable, never counted into room math.

    face_value_cents: optional list price for this product — fills a
    missing amount (face minus any parsed coupon discount) so
    percentage rewards can compute from exports with no price column.
    """

    __tablename__ = "sale_type_mappings"
    __table_args__ = (UniqueConstraint("event_id", "raw_label", name="uq_sale_type_mappings_event_label"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    raw_label = Column(String, nullable=False)
    seating_category_id = Column(
        UUID(as_uuid=True), ForeignKey("seating_categories.id", ondelete="SET NULL"), nullable=True
    )
    face_value_cents = Column(Integer, nullable=True)
    is_admission = Column(Boolean, nullable=False, default=True, server_default="true")
    all_days = Column(Boolean, nullable=False, default=False, server_default="false")  # 0050: weekend package label
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)