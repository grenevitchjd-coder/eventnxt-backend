# eventnxt-backend: app/models/zone_section.py
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class ZoneSection(Base):
    """
    One member piece of a seating pool — "Row 1 · Section A · 25 seats"
    or "Section B · 6 tables × 8". Entered inline from the ticket-type
    composer. The parent pool's capacity is derived as the sum of its
    sections whenever sections exist; seat records (assigned-seat
    selling) generate from these rows.
    """

    __tablename__ = "zone_sections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    seating_category_id = Column(
        UUID(as_uuid=True), ForeignKey("seating_categories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    section_label = Column(String, nullable=False)
    row_label = Column(String, nullable=True)
    capacity = Column(Integer, nullable=False)
    table_count = Column(Integer, nullable=True)
    seats_per_table = Column(Integer, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)