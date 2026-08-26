import uuid
from datetime import datetime, time
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
# Two modes: one-time (a specific event_datetime) or daily-recurring (just
# a time_of_day, applied to every day of the event's real date range).
# The organizer-facing list/create/delete endpoints work with the raw
# pattern (one row per item, however it repeats) — expansion into concrete
# per-day instances happens only for the PUBLIC page (see
# PublicScheduleItemResponse below), so editing/deleting a daily item acts
# on the whole pattern, not one expanded occurrence.


class EventProfileScheduleItemCreateRequest(BaseModel):
    label: str
    is_recurring: bool = False
    event_datetime: Optional[datetime] = None  # required if is_recurring=False
    time_of_day: Optional[time] = None  # required if is_recurring=True
    sort_order: int = 0

    @model_validator(mode="after")
    def check_fields_match_recurrence(self):
        if self.is_recurring:
            if self.time_of_day is None:
                raise ValueError("A daily schedule item needs a time of day.")
        else:
            if self.event_datetime is None:
                raise ValueError("A one-time schedule item needs a specific date and time.")
        return self


class EventProfileScheduleItemResponse(BaseModel):
    id: uuid.UUID
    label: str
    is_recurring: bool
    event_datetime: Optional[datetime] = None
    time_of_day: Optional[time] = None
    sort_order: int

    class Config:
        from_attributes = True


class PublicScheduleItemResponse(BaseModel):
    """A one-time item on the public page — has its own specific date."""

    label: str
    event_datetime: datetime


class PublicDailyScheduleItemResponse(BaseModel):
    """
    A recurring daily pattern shown ONCE, not expanded per day — just a
    label and a plain wall-clock time ("18:00"), no date attached and no
    timezone conversion anywhere in the pipeline. This is deliberate: a
    time like "Doors Open at 6:00 PM" is venue-local wall-clock time, not
    a moment in UTC — converting it through a timezone and back was
    exactly the bug that showed 6:00 PM as 11:00 AM to some viewers.
    """

    label: str
    time_of_day: str  # "HH:MM", 24-hour, formatted for direct display


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
    daily_schedule: list[PublicDailyScheduleItemResponse] = []
    schedule: list[PublicScheduleItemResponse] = []
    photos: list[EventProfilePhotoResponse] = []