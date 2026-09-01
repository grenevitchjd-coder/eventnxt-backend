# eventnxt-backend: app/schemas/event_settings.py
from datetime import datetime
from typing import Optional
import uuid

from pydantic import BaseModel


class EventSettingsResponse(BaseModel):
    event_id: uuid.UUID
    ticketing_mode: str
    sales_source: str
    comp_delivery: str
    ticket_span: str = "single_day"
    pricing_mode: str = "uniform"
    seating_mode: str = "uniform"
    first_day: Optional[str] = None
    last_day: Optional[str] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class EventSettingsUpdateRequest(BaseModel):
    """Partial update — send only what changed. Values are validated in
    the router against the allowed sets so a typo'd mode can't silently
    reshape the whole dashboard."""

    ticketing_mode: Optional[str] = None
    sales_source: Optional[str] = None
    comp_delivery: Optional[str] = None
    ticket_span: Optional[str] = None
    pricing_mode: Optional[str] = None
    seating_mode: Optional[str] = None
    first_day: Optional[str] = None
    last_day: Optional[str] = None