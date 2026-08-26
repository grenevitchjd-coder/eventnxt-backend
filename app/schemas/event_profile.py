import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, model_validator


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
    logo_url: Optional[str] = None
    external_ticket_url: Optional[str] = None
    cached_start_date: Optional[datetime] = None
    cached_end_date: Optional[datetime] = None
    slug: str
    is_published: bool
    published_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ---------- Links (contacts / socials) ----------


class EventProfileLinkCreateRequest(BaseModel):
    kind: Literal["contact", "social"]
    label: str
    value: str

    @model_validator(mode="after")
    def check_value_matches_kind(self):
        if self.kind == "contact":
            import re

            if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", self.value):
                raise ValueError("For a contact link, value must be a valid email address.")
        else:
            if not (self.value.startswith("http://") or self.value.startswith("https://")):
                raise ValueError("For a social link, value must be a URL starting with http:// or https://.")
        return self


class EventProfileLinkResponse(BaseModel):
    id: uuid.UUID
    kind: str
    label: str
    value: str
    sort_order: int

    class Config:
        from_attributes = True


# ---------- Schedule items ----------


class EventProfileScheduleItemCreateRequest(BaseModel):
    label: str
    event_datetime: datetime
    sort_order: int = 0


class EventProfileScheduleItemResponse(BaseModel):
    id: uuid.UUID
    label: str
    event_datetime: datetime
    sort_order: int

    class Config:
        from_attributes = True


# ---------- Gallery photos ----------


class EventProfilePhotoResponse(BaseModel):
    id: uuid.UUID
    url: str
    sort_order: int

    class Config:
        from_attributes = True


# ---------- Public page ----------


class PublicEventProfileResponse(BaseModel):
    """Deliberately narrower than EventProfileResponse — no internal IDs
    or timestamps leaked to the public page."""

    title: str
    description: Optional[str] = None
    address: Optional[str] = None
    banner_photo_url: Optional[str] = None
    logo_url: Optional[str] = None
    external_ticket_url: Optional[str] = None
    cached_start_date: Optional[datetime] = None
    cached_end_date: Optional[datetime] = None
    links: list[EventProfileLinkResponse] = []
    schedule: list[EventProfileScheduleItemResponse] = []
    photos: list[EventProfilePhotoResponse] = []