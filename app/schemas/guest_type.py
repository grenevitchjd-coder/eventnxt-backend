import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class GuestTypeCreateRequest(BaseModel):
    name: str
    default_ticket_count: Optional[int] = Field(default=None, ge=0)
    default_valid_dates: Optional[List[str]] = None


class GuestTypeUpdateRequest(BaseModel):
    name: str
    default_ticket_count: Optional[int] = Field(default=None, ge=0)
    default_valid_dates: Optional[List[str]] = None


class GuestTypeResponse(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    name: str
    default_ticket_count: Optional[int] = None
    default_valid_dates: Optional[List[str]] = None
    created_at: datetime

    class Config:
        from_attributes = True


class GuestTypeSeatingPriorityCreateRequest(BaseModel):
    seating_category_id: uuid.UUID
    # priority_order is NOT accepted here — new entries always append to the
    # end of the list (auto-computed server-side). To reorder, remove and
    # re-add in the desired sequence.


class GuestTypeSeatingPriorityResponse(BaseModel):
    id: uuid.UUID
    seating_category_id: uuid.UUID
    priority_order: int

    class Config:
        from_attributes = True