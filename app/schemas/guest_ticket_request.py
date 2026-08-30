# eventnxt-backend: app/schemas/guest_ticket_request.py
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class GuestTicketRequestResponse(BaseModel):
    id: uuid.UUID
    guest_id: uuid.UUID
    guest_name: str
    guest_email: str
    current_party_size: int
    quantity: int
    note: Optional[str] = None
    status: str
    created_at: datetime
    resolved_at: Optional[datetime] = None