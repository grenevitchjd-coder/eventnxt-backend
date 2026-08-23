import uuid
from datetime import datetime

from pydantic import BaseModel


class GuestTypeCreateRequest(BaseModel):
    name: str


class GuestTypeResponse(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    created_at: datetime

    class Config:
        from_attributes = True