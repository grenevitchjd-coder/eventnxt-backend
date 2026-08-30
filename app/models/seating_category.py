import uuid

from sqlalchemy import Column, String, Integer, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class SeatingCategory(Base):
    """
    A capacity pool for a specific event (Front Center, Row 4, Standing
    Room, etc.). Originally comp-ticket allocation only; with native
    ticket sales it's now the shared pool that BOTH guest-list holds and
    paid ticket types (via TicketType.seating_category_id) drain and
    reconcile against — one availability truth, no double-selling.
    event_id is a stored reference to Events360's Event, not a real
    foreign key (separate databases).

    sales_grain records HOW this category sells: 'ga' (a pool — buyer
    gets "one of these seats") or 'seat' (specific assigned seats). The
    grain is per-category on purpose — one room can mix them ("Row 1"
    sells assigned seats while "GA — Rows 3&4" sells as a pool). In v1
    the grain is recorded but visually enforced only as GA; the seat
    picker that makes 'seat' interactive arrives with the venue-map
    phase.
    """

    __tablename__ = "seating_categories"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    name = Column(String, nullable=False)  # e.g. "Front Center", "Row 4", "Standing Room"
    capacity = Column(Integer, nullable=False)
    sales_grain = Column(String, nullable=False, default="ga", server_default="ga")  # 'ga' | 'seat'
    created_at = Column(DateTime(timezone=True), server_default=func.now())