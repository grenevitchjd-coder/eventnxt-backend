import uuid
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, EmailStr, Field


class TicketAllotmentDayItem(BaseModel):
    date: str
    quantity: int = Field(ge=0)


class GuestCreateRequest(BaseModel):
    name: str
    email: EmailStr
    guest_type_id: uuid.UUID
    seating_category_id: Optional[uuid.UUID] = None
    allocation_status: Literal["confirmed", "pending", "declined"] = "confirmed"
    party_size: int = Field(default=1, ge=1)
    visit_date: Optional[str] = None
    # Per-guest override of the guest type's default ticket allotment.
    # Omit entirely to inherit the type's default. Provide a list
    # (including an empty one, to mean "explicitly nothing to give out")
    # to override it — see Guest.ticket_allotment_overridden.
    ticket_allotment: Optional[List[TicketAllotmentDayItem]] = None


class GuestUpdateRequest(BaseModel):
    name: str
    email: EmailStr
    guest_type_id: uuid.UUID
    seating_category_id: Optional[uuid.UUID] = None
    allocation_status: Literal["confirmed", "pending", "declined"] = "confirmed"
    party_size: int = Field(default=1, ge=1)
    visit_date: Optional[str] = None
    ticket_allotment: Optional[List[TicketAllotmentDayItem]] = None


class GuestResponse(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    name: str
    email: str
    guest_type_id: uuid.UUID
    seating_category_id: Optional[uuid.UUID] = None
    allocation_status: str
    party_size: int
    ticket_allotment_overridden: bool
    ticket_allotment: List[TicketAllotmentDayItem] = []
    visit_date: Optional[str] = None
    allocated_by_guest_id: Optional[uuid.UUID] = None
    rsvp_token: str
    rsvp_confirmed: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True