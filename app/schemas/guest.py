import uuid
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, EmailStr, Field


class GuestCreateRequest(BaseModel):
    name: str
    email: EmailStr
    guest_type_id: uuid.UUID
    seating_category_id: Optional[uuid.UUID] = None
    allocation_status: Literal["confirmed", "pending", "declined"] = "confirmed"
    party_size: int = Field(default=1, ge=1)
    # Allotment fields — only meaningful on a guest who's meant to hold and
    # distribute tickets (models, sponsors). Omit for an ordinary guest.
    allotment_ticket_count: Optional[int] = Field(default=None, ge=0)
    allotment_valid_dates: Optional[List[str]] = None
    visit_date: Optional[str] = None


class GuestUpdateRequest(BaseModel):
    name: str
    email: EmailStr
    guest_type_id: uuid.UUID
    seating_category_id: Optional[uuid.UUID] = None
    allocation_status: Literal["confirmed", "pending", "declined"] = "confirmed"
    party_size: int = Field(default=1, ge=1)
    allotment_ticket_count: Optional[int] = Field(default=None, ge=0)
    allotment_valid_dates: Optional[List[str]] = None
    visit_date: Optional[str] = None


class GuestResponse(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    name: str
    email: str
    guest_type_id: uuid.UUID
    seating_category_id: Optional[uuid.UUID] = None
    allocation_status: str
    party_size: int
    allotment_ticket_count: Optional[int] = None
    allotment_valid_dates: Optional[List[str]] = None
    visit_date: Optional[str] = None
    allocated_by_guest_id: Optional[uuid.UUID] = None
    rsvp_token: str
    rsvp_confirmed: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True