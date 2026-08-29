import enum
import uuid

from sqlalchemy import Column, DateTime, Enum as SAEnum, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class SalesPlatform(str, enum.Enum):
    """
    A curated list of known ticketing/box-office platforms an organizer
    might sell through, plus a generic fallback. Whether a given platform
    has a real, working live-data integration is tracked separately (see
    app/services/sales_platforms.py) — every platform here can fall back
    to CSV upload regardless, so adding this enum value doesn't commit to
    building an integration for it; it just makes it selectable and
    self-documenting in the setup page ("you're on Eventbrite — no live
    connection yet, so here's CSV upload for now").
    """

    CUSTOM_CSV = "custom_csv"  # no named platform — just upload exports
    EVENTBRITE = "eventbrite"
    TICKETMASTER = "ticketmaster"
    SQUARE = "square"
    STRIPE = "stripe"
    OTHER = "other"


class SalesConfig(Base):
    """
    One row per event — which box-office platform the organizer says
    they're selling through. Drives which reconciliation path (CSV vs. a
    future live integration) the sales/promo-code UI offers. event_id is
    a stored reference to Events360's Event, not a real foreign key.
    """

    __tablename__ = "sales_configs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), nullable=False, unique=True, index=True)
    platform = Column(
        SAEnum(SalesPlatform, name="sales_platform", values_callable=lambda e: [x.value for x in e]),
        nullable=False,
        default=SalesPlatform.CUSTOM_CSV,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())