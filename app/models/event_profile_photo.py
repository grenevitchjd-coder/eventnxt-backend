import uuid

from sqlalchemy import Column, String, Integer, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base

MAX_GALLERY_PHOTOS = 3


class EventProfilePhoto(Base):
    """
    One photo in the event's small gallery, in addition to the banner and
    logo. Capped at MAX_GALLERY_PHOTOS — enforced in the router, not the
    database, since it's a soft product limit, not a data-integrity rule.
    """

    __tablename__ = "event_profile_photos"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_profile_id = Column(UUID(as_uuid=True), ForeignKey("event_profiles.id"), nullable=False)
    url = Column(String, nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())