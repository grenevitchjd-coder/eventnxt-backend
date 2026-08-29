import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class GuestTypeCreateRequest(BaseModel):
    name: str


class GuestTypeUpdateRequest(BaseModel):
    name: str


class GuestTypeResponse(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    name: str
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


class TicketAllotmentDayUpsertRequest(BaseModel):
    quantity: int = Field(ge=0)


class TicketAllotmentDayResponse(BaseModel):
    date: str
    quantity: int

    class Config:
        from_attributes = True