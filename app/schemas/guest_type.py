import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class GuestTypeCreateRequest(BaseModel):
    name: str
    default_seating_category_id: Optional[uuid.UUID] = None


class GuestTypeUpdateRequest(BaseModel):
    name: str
    default_seating_category_id: Optional[uuid.UUID] = None


class GuestTypeResponse(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    name: str
    default_seating_category_id: Optional[uuid.UUID] = None
    created_at: datetime

    class Config:
        from_attributes = True