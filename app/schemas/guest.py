import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, EmailStr


class GuestCreateRequest(BaseModel):
    name: str
    email: EmailStr
    guest_type_id: uuid.UUID
    seating_category_id: Optional[uuid.UUID] = None
    allocation_status: Literal["confirmed", "pending"] = "confirmed"


class GuestResponse(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    name: str
    email: str
    guest_type_id: uuid.UUID
    seating_category_id: Optional[uuid.UUID] = None
    allocation_status: str
    rsvp_token: str
    rsvp_confirmed: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True