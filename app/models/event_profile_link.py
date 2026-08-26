import enum
import uuid

from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class LinkKind(str, enum.Enum):
    CONTACT = "contact"  # value is an email address
    SOCIAL = "social"  # value is a URL


class EventProfileLink(Base):
    """
    A labeled contact email or social link — "Sponsorships: sponsors@...",
    "Instagram: https://instagram.com/...". Unlimited entries, fully
    organizer-defined labels, same shape for both kinds (kind determines
    how `value` is validated and how the public page renders it).
    """

    __tablename__ = "event_profile_links"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_profile_id = Column(UUID(as_uuid=True), ForeignKey("event_profiles.id"), nullable=False)
    kind = Column(
        SAEnum(LinkKind, name="link_kind", values_callable=lambda enum_cls: [e.value for e in enum_cls]),
        nullable=False,
    )
    label = Column(String, nullable=False)  # e.g. "Sponsorships", "Instagram"
    value = Column(String, nullable=False)  # email address, or URL
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())