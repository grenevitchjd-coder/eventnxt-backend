import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class EventProfileCreateOrUpdateRequest(BaseModel):
    title: str
    description: Optional[str] = None
    address: Optional[str] = None
    external_ticket_url: Optional[str] = None
    slug: Optional[str] = None  # if omitted, auto-generated from title


class EventProfileResponse(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    title: str
    description: Optional[str] = None
    address: Optional[str] = None
    banner_photo_url: Optional[str] = None
    external_ticket_url: Optional[str] = None
    slug: str
    is_published: bool
    published_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class PublicEventProfileResponse(BaseModel):
    """Deliberately narrower than EventProfileResponse — no internal IDs
    or timestamps leaked to the public page."""

    title: str
    description: Optional[str] = None
    address: Optional[str] = None
    banner_photo_url: Optional[str] = None
    external_ticket_url: Optional[str] = None