# eventnxt-backend: app/models/seat.py
import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class Seat(Base):
    """
    One physical seat in an assigned pool — generated from zone_sections,
    never hand-typed. Identity is (pool, section_label, row_label,
    seat_number): section edits re-link surviving seats to their new
    zone_section row instead of destroying them, so a sold seat keeps
    its ticket through any relabeling of capacities around it.

    "Taken" is derived, never stored: a VALID ticket pointing here, or an
    order_item_seats row on an unexpired pending / paid order. Expiry and
    refunds therefore free seats with zero bookkeeping — the same
    property the quantity holds have. is_blocked is the organizer's hold
    switch — a broken chair, or a labeled reservation (block_label:
    "Press") that Slice B hands to a specific guest.
    """

    __tablename__ = "seats"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    seating_category_id = Column(
        UUID(as_uuid=True), ForeignKey("seating_categories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    zone_section_id = Column(UUID(as_uuid=True), ForeignKey("zone_sections.id", ondelete="SET NULL"), nullable=True)
    section_label = Column(String, nullable=False)
    row_label = Column(String, nullable=True)
    seat_number = Column(Integer, nullable=False)
    is_blocked = Column(Boolean, nullable=False, default=False)
    block_label = Column(String, nullable=True)  # why it's held: "Press", "Sponsor hold"…
    # The comp guest this seat is assigned to. Assignment implies
    # reservation (assign sets is_blocked too) so sale predicates never
    # change. SET NULL on guest delete — the seat stays reserved.
    guest_id = Column(UUID(as_uuid=True), ForeignKey("guests.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    @property
    def label(self) -> str:
        bits = [f"Section {self.section_label}"]
        if self.row_label:
            bits.append(self.row_label)
        bits.append(f"Seat {self.seat_number}")
        return " · ".join(bits)


class OrderItemSeat(Base):
    """A buyer's chosen seat on an order item — the seat-level hold."""

    __tablename__ = "order_item_seats"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_item_id = Column(UUID(as_uuid=True), ForeignKey("order_items.id", ondelete="CASCADE"), nullable=False, index=True)
    seat_id = Column(UUID(as_uuid=True), ForeignKey("seats.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)