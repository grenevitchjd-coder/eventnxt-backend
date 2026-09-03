# eventnxt-backend: app/schemas/guest.py
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
    section_label: Optional[str] = None  # section within the pool; needs seating_category_id
    allocation_status: Literal["confirmed", "pending", "declined"] = "confirmed"
    party_size: int = Field(default=1, ge=1)
    visit_date: Optional[str] = None
    hold_timing: Literal["now", "on_confirm", "later"] = "now"
    cohort_together: bool = True
    perks: Optional[str] = None
    comments: Optional[str] = None
    ticket_allotment: Optional[List[TicketAllotmentDayItem]] = None
    guest_mode: Optional[Literal["invite", "distribute", "select"]] = None


class GuestUpdateRequest(BaseModel):
    name: str
    email: EmailStr
    guest_type_id: uuid.UUID
    seating_category_id: Optional[uuid.UUID] = None
    section_label: Optional[str] = None
    allocation_status: Literal["confirmed", "pending", "declined"] = "confirmed"
    party_size: int = Field(default=1, ge=1)
    visit_date: Optional[str] = None
    hold_timing: Literal["now", "on_confirm", "later"] = "now"
    cohort_together: bool = True
    perks: Optional[str] = None
    comments: Optional[str] = None
    ticket_allotment: Optional[List[TicketAllotmentDayItem]] = None
    guest_mode: Optional[Literal["invite", "distribute", "select"]] = None


class GuestResponse(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    name: str
    email: str
    guest_type_id: uuid.UUID
    seating_category_id: Optional[uuid.UUID] = None
    section_label: Optional[str] = None
    allocation_status: str
    party_size: int
    perks: Optional[str] = None
    comments: Optional[str] = None
    ticket_allotment_overridden: bool
    ticket_allotment: List[TicketAllotmentDayItem] = []
    allotment_total: int = 0
    allotment_distributed: int = 0
    visit_date: Optional[str] = None
    hold_timing: Literal["now", "on_confirm", "later"] = "now"
    cohort_together: bool = True
    allocated_by_guest_id: Optional[uuid.UUID] = None
    rsvp_token: str
    rsvp_confirmed: Optional[str] = None
    guest_mode: Optional[str] = None
    effective_mode: str = "invite"
    needs_seating: bool = False
    ticket_count: int = 0
    seat_labels: List[str] = []  # assigned seats, display order
    link_sent_at: Optional[datetime] = None
    tickets_sent_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class GuestSeatsAssignRequest(BaseModel):
    """Wholesale replace of the guest's assigned seats (empty = unassign
    all; released seats stay reserved)."""

    seat_ids: List[uuid.UUID] = []


class GuestSentStatusRequest(BaseModel):
    # Which manual marker to flip: the RSVP-link one (default, the
    # original behavior) or the external-ticketing tickets-sent one.
    marker: Literal["link", "tickets"] = "link"
    sent: bool