import uuid

from sqlalchemy import Column, String, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class GuestType(Base):
    """
    Org-level, reusable across events (Celebrity, Sponsor, Volunteer,
    Model, etc.) — drives which email template applies (later slice) and
    is a separate concept from SeatingCategory (which is event-specific
    and capacity-limited). organization_id is a stored reference to
    Events360's Organization, not a real foreign key (separate databases).
    """

    __tablename__ = "guest_types"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    name = Column(String, nullable=False)  # e.g. "Celebrity", "Sponsor", "Volunteer", "Model"
    created_at = Column(DateTime(timezone=True), server_default=func.now())