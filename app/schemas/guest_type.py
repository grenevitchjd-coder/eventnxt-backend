# eventnxt-backend: app/schemas/guest_type.py
import uuid
from typing import Literal, Optional
from datetime import datetime

from pydantic import BaseModel, Field


GUEST_MODE_FIELD = Field(default=None, pattern="^(invite|distribute|select)$")


class GuestTypeCreateRequest(BaseModel):
    name: str
    guest_mode: str | None = GUEST_MODE_FIELD
    # Shape defaults (0039): dates never live on the type — one type
    # covers a Thu-only offer and a Fri-only offer.
    day_scope: str | None = None  # 'single' / 'specific' / 'choose' / 'all'
    default_ticket_count: int | None = None
    default_hold_timing: str | None = None  # 'now' / 'on_confirm' / 'later'
    default_spend_total: int | None = None


class GuestTypeUpdateRequest(BaseModel):
    name: str
    guest_mode: str | None = GUEST_MODE_FIELD
    # Shape defaults (0039): dates never live on the type — one type
    # covers a Thu-only offer and a Fri-only offer.
    day_scope: str | None = None  # 'single' / 'specific' / 'choose' / 'all'
    default_ticket_count: int | None = None
    default_hold_timing: str | None = None  # 'now' / 'on_confirm' / 'later'
    default_spend_total: int | None = None


class GuestTypeResponse(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    name: str
    guest_mode: str | None = None
    day_scope: str | None = None
    default_ticket_count: int | None = None
    default_hold_timing: str | None = None
    default_spend_total: int | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class GuestTypeSeatingPriorityCreateRequest(BaseModel):
    seating_category_id: uuid.UUID
    # None = the whole pool; a label = only that section of the pool.
    section_label: Optional[str] = None
    # A set of permitted sections ("C,D,E" as a list) + how to fill them.
    allowed_sections: Optional[list[str]] = None
    placement: Literal["together", "spread"] = "together"
    # priority_order is NOT accepted here — new entries always append to the
    # end of the list (auto-computed server-side). To reorder, remove and
    # re-add in the desired sequence.


class GuestTypeSeatingPriorityResponse(BaseModel):
    id: uuid.UUID
    seating_category_id: uuid.UUID
    section_label: Optional[str] = None
    allowed_sections: Optional[str] = None  # comma-joined, as stored
    placement: str = "together"
    priority_order: int

    class Config:
        from_attributes = True


class TicketAllotmentDayUpsertRequest(BaseModel):
    quantity: int = Field(ge=0)


class TicketAllotmentDayResponse(BaseModel):
    date: str
    quantity: int

    class Config:
        from_attributes = True