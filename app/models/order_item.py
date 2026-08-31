"""eventnxt-backend: app/models/order_item.py"""

import uuid

from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class OrderItem(Base):
    """
    One line of an order: N tickets of one ticket type.

    unit_price_cents and ticket_type_name are SNAPSHOTS taken at order
    creation — if the organizer renames or reprices the ticket type
    tomorrow, this order's history still says exactly what was bought and
    for how much. The ticket_type_id stays as the live link for
    inventory counting; the snapshots are for the record.
    """

    __tablename__ = "order_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id = Column(UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False, index=True)
    ticket_type_id = Column(UUID(as_uuid=True), ForeignKey("ticket_types.id"), nullable=False, index=True)

    quantity = Column(Integer, nullable=False)
    unit_price_cents = Column(Integer, nullable=False)  # snapshot
    ticket_type_name = Column(String, nullable=False)
    # Section choice (sectioned, unassigned types): FK survives section
    # restructures via SET NULL; the label snapshot survives renames.
    zone_section_id = Column(UUID(as_uuid=True), ForeignKey("zone_sections.id", ondelete="SET NULL"), nullable=True, index=True)
    section_label = Column(String, nullable=True)  # snapshot