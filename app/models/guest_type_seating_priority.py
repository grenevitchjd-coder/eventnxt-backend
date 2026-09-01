import uuid

from sqlalchemy import Column, Integer, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class GuestTypeSeatingPriority(Base):
    """
    One entry in a guest type's ORDERED seating preference list — "try 2A
    first, then 2B, then 4A." When a guest of this type is added without
    an explicit seat, the system walks this list in priority_order and
    assigns the first category that still has room. Replaces the earlier
    single default_seating_category_id, which couldn't express "prefer
    this, but fall back to that if it's full."
    """

    __tablename__ = "guest_type_seating_priorities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    guest_type_id = Column(UUID(as_uuid=True), ForeignKey("guest_types.id"), nullable=False)
    seating_category_id = Column(UUID(as_uuid=True), ForeignKey("seating_categories.id"), nullable=False)
    priority_order = Column(Integer, nullable=False)  # 0 = highest priority (tried first)
    # NULL = the whole pool; a label = only that section of the pool.
    # A LABEL, not a zone_sections FK — sections are replaced wholesale
    # on every structure save, so labels are the durable identity (same
    # philosophy as seats). Labels that stop existing are skipped by the
    # resolver.
    section_label = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())